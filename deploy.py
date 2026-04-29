"""部署脚本：使用系统 OpenSSH 和 npm，不依赖本地虚拟环境。"""
from collections.abc import Callable
from datetime import datetime, timezone
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
BACKEND_DIR = PROJECT_ROOT / "backend"


def get_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return max(minimum, int(raw_value))
    except ValueError:
        return default


def load_local_env(env_path: Path):
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_local_env(PROJECT_ROOT / ".env")

REMOTE_HOST = os.getenv("DEPLOY_REMOTE_HOST", "").strip()
REMOTE_PORT = int(os.getenv("DEPLOY_REMOTE_PORT", "22"))
REMOTE_USER = os.getenv("DEPLOY_REMOTE_USER", "").strip()
REMOTE_KEY_PATH = os.getenv("DEPLOY_SSH_KEY_PATH", "").strip().replace("\\", "/") or None
REMOTE_BASE = os.getenv("DEPLOY_REMOTE_BASE", "").strip()
PYTHON_BIN = os.getenv("DEPLOY_PYTHON_BIN", "").strip()
DEPLOY_BACKEND_PORT = os.getenv(
    "DEPLOY_BACKEND_PORT", os.getenv("BACKEND_PORT", "18100")
).strip()
FRONTEND_RELEASES_KEEP = get_int_env("DEPLOY_FRONTEND_RELEASES_KEEP", 4)
BACKEND_RELEASES_KEEP = get_int_env("DEPLOY_BACKEND_RELEASES_KEEP", 4)

NPM_CMD = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
SSH_CMD = shutil.which("ssh")
SCP_CMD = shutil.which("scp")
NPM_BIN = NPM_CMD or "npm"
SSH_BIN = SSH_CMD or "ssh"
SCP_BIN = SCP_CMD or "scp"

BACKEND_RELEASE_EXCLUDED_PARTS = {
    ".deploy",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "static",
    "logs",
}
BACKEND_RELEASE_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def validate_deploy_settings():
    required_settings = {
        "DEPLOY_REMOTE_HOST": REMOTE_HOST,
        "DEPLOY_REMOTE_USER": REMOTE_USER,
        "DEPLOY_REMOTE_BASE": REMOTE_BASE,
        "DEPLOY_PYTHON_BIN": PYTHON_BIN,
        "DEPLOY_BACKEND_PORT": DEPLOY_BACKEND_PORT,
    }
    missing_settings = [name for name, value in required_settings.items() if not value]
    if missing_settings:
        raise RuntimeError("缺少部署环境变量: " + ", ".join(missing_settings))
    if REMOTE_KEY_PATH and not Path(REMOTE_KEY_PATH).exists():
        raise RuntimeError(f"SSH 私钥不存在: {REMOTE_KEY_PATH}")
    if not SSH_CMD or not SCP_CMD:
        raise RuntimeError("未找到系统 ssh/scp，请先安装 OpenSSH Client")
    if not NPM_CMD:
        raise RuntimeError("未找到 npm，请先安装 Node.js")


def format_command(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def run_streaming_command(args: list[str], cwd: Path | None = None):
    print(f"  > {format_command(args)}")
    result = subprocess.run(args, cwd=cwd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败({result.returncode}): {format_command(args)}")


def run_captured_command(args: list[str], cwd: Path | None = None, check: bool = True):
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if check and result.returncode != 0:
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        raise RuntimeError(
            f"命令失败({result.returncode}): {format_command(args)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    return result


def remote_login() -> str:
    return f"{REMOTE_USER}@{REMOTE_HOST}"


def remote_shell_quote(value: str | Path) -> str:
    return shlex.quote(str(value).replace("\\", "/"))


def ssh_base_args() -> list[str]:
    args = [SSH_BIN, "-p", str(REMOTE_PORT), "-o", "StrictHostKeyChecking=accept-new"]
    if REMOTE_KEY_PATH:
        args.extend(["-i", REMOTE_KEY_PATH, "-o", "BatchMode=yes"])
    args.append(remote_login())
    return args


def scp_base_args() -> list[str]:
    args = [SCP_BIN, "-P", str(REMOTE_PORT), "-o", "StrictHostKeyChecking=accept-new"]
    if REMOTE_KEY_PATH:
        args.extend(["-i", REMOTE_KEY_PATH, "-o", "BatchMode=yes"])
    return args


def run_remote_command(command: str, check: bool = True):
    return run_captured_command(
        ssh_base_args() + [f"bash -lc {shlex.quote(command)}"],
        check=check,
    )


def ensure_frontend_build():
    print("=== 构建前端 ===")
    node_modules_dir = FRONTEND_DIR / "node_modules"
    if not node_modules_dir.is_dir():
        print("  前端依赖不存在，执行 npm install")
        run_streaming_command([NPM_BIN, "install"], cwd=FRONTEND_DIR)
    print("  执行 npm run build")
    run_streaming_command([NPM_BIN, "run", "build"], cwd=FRONTEND_DIR)
    if not FRONTEND_DIST.is_dir():
        raise RuntimeError(f"前端构建产物不存在: {FRONTEND_DIST}")


def ensure_remote_directory(remote_dir: str):
    run_remote_command(f"mkdir -p {remote_shell_quote(remote_dir)}")


def generate_release_id() -> str:
    return datetime.now(timezone.utc).strftime("release-%Y%m%d%H%M%S-%f")


def acquire_remote_deploy_lock():
    print("\n=== 获取远端部署锁 ===")
    lock_root = f"{REMOTE_BASE}/.deploy"
    lock_dir = f"{lock_root}/deploy.lock"
    command = f'''
set -e
LOCK_ROOT={remote_shell_quote(lock_root)}
LOCK_DIR={remote_shell_quote(lock_dir)}

mkdir -p "$LOCK_ROOT"

if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' {remote_shell_quote(datetime.now(timezone.utc).isoformat())} > "$LOCK_DIR/acquired_at"
else
    echo "已有部署任务在运行，请先清理锁目录: $LOCK_DIR" >&2
    exit 1
fi
'''
    run_remote_command(command)


def release_remote_deploy_lock():
    lock_dir = f"{REMOTE_BASE}/.deploy/deploy.lock"
    run_remote_command(f"rm -rf {remote_shell_quote(lock_dir)}", check=False)


def should_include_backend_release_file(local_path: Path) -> bool:
    relative_parts = local_path.relative_to(BACKEND_DIR).parts
    if any(part in BACKEND_RELEASE_EXCLUDED_PARTS for part in relative_parts[:-1]):
        return False
    if local_path.suffix.lower() in BACKEND_RELEASE_EXCLUDED_SUFFIXES:
        return False
    return True


def upload_file(local_path: Path, remote_path: str):
    ensure_remote_directory(str(Path(remote_path).parent).replace("\\", "/"))
    run_streaming_command(scp_base_args() + [str(local_path), f"{remote_login()}:{remote_path}"])
    print(f"  [OK] {remote_path} ({local_path.stat().st_size:,} bytes)")


def upload_dir(
    local_dir: Path,
    remote_dir: str,
    *,
    index_last: bool = False,
    file_filter: Callable[[Path], bool] | None = None,
):
    files = sorted(
        path
        for path in local_dir.rglob("*")
        if path.is_file() and (file_filter(path) if file_filter else True)
    )
    if index_last:
        files.sort(key=lambda path: path.name == "index.html")

    for local_path in files:
        relative_path = local_path.relative_to(local_dir).as_posix()
        upload_file(local_path, f"{remote_dir}/{relative_path}")


def prepare_backend_release(release_id: str) -> str:
    print("\n=== 准备后端 release ===")
    backend_root = f"{REMOTE_BASE}/backend"
    releases_root = f"{backend_root}/.deploy/releases"
    release_dir = f"{releases_root}/{release_id}"
    command = (
        f"rm -rf {remote_shell_quote(release_dir)} && "
        f"mkdir -p {remote_shell_quote(releases_root)} "
        f"{remote_shell_quote(release_dir)} "
        f"{remote_shell_quote(f'{backend_root}/static')} "
        f"{remote_shell_quote(f'{backend_root}/logs')}"
    )
    run_remote_command(command)
    return release_dir


def upload_backend_release(release_dir: str):
    print("\n=== 上传后端 release ===")
    upload_dir(BACKEND_DIR, release_dir, file_filter=should_include_backend_release_file)


def get_current_symlink_target(symlink_path: str) -> str | None:
    result = run_remote_command(
        f"if [ -L {remote_shell_quote(symlink_path)} ]; then readlink -f {remote_shell_quote(symlink_path)}; fi",
        check=False,
    )
    target = result.stdout.strip()
    return target or None


def switch_symlink(symlink_path: str, target_dir: str, label: str):
    print(f"\n=== {label} ===")
    next_symlink_path = f"{symlink_path}.__next"
    command = f'''
set -e
TARGET_DIR={remote_shell_quote(target_dir)}
SYMLINK_PATH={remote_shell_quote(symlink_path)}
NEXT_SYMLINK_PATH={remote_shell_quote(next_symlink_path)}

if [ ! -d "$TARGET_DIR" ]; then
    echo "目标目录不存在: $TARGET_DIR" >&2
    exit 1
fi

if [ -e "$SYMLINK_PATH" ] && [ ! -L "$SYMLINK_PATH" ]; then
    echo "目标路径已存在且不是 symlink: $SYMLINK_PATH" >&2
    exit 1
fi

ln -sfn "$TARGET_DIR" "$NEXT_SYMLINK_PATH"
mv -Tf "$NEXT_SYMLINK_PATH" "$SYMLINK_PATH"
readlink -f "$SYMLINK_PATH"
'''
    result = run_remote_command(command)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def activate_backend_release(release_id: str):
    backend_root = f"{REMOTE_BASE}/backend"
    release_dir = f"{backend_root}/.deploy/releases/{release_id}"
    current_path = f"{backend_root}/current"
    switch_symlink(current_path, release_dir, "切换后端 release")


def restore_backend_release(target_dir: str):
    backend_root = f"{REMOTE_BASE}/backend"
    current_path = f"{backend_root}/current"
    switch_symlink(current_path, target_dir, "回滚后端 release")


def prepare_frontend_release(release_id: str) -> str:
    print("\n=== 准备前端 release ===")
    frontend_root = f"{REMOTE_BASE}/frontend"
    releases_root = f"{frontend_root}/.deploy/releases"
    release_dir = f"{releases_root}/{release_id}"
    command = (
        f"rm -rf {remote_shell_quote(release_dir)} && "
        f"mkdir -p {remote_shell_quote(releases_root)} {remote_shell_quote(release_dir)}"
    )
    run_remote_command(command)
    return release_dir


def upload_frontend_dist(release_dir: str):
    print("\n=== 上传前端 dist release ===")
    upload_dir(FRONTEND_DIST, release_dir, index_last=True)


def activate_frontend_release(release_id: str):
    print("\n=== 切换前端 release ===")
    frontend_root = f"{REMOTE_BASE}/frontend"
    releases_root = f"{frontend_root}/.deploy/releases"
    release_dir = f"{releases_root}/{release_id}"
    dist_path = f"{frontend_root}/dist"
    next_dist_path = f"{frontend_root}/dist.__next"
    command = f'''
set -e
RELEASES_ROOT={remote_shell_quote(releases_root)}
RELEASE_DIR={remote_shell_quote(release_dir)}
DIST_PATH={remote_shell_quote(dist_path)}
NEXT_DIST_PATH={remote_shell_quote(next_dist_path)}

mkdir -p "$RELEASES_ROOT"

if [ ! -d "$RELEASE_DIR" ]; then
    echo "前端 release 不存在: $RELEASE_DIR" >&2
    exit 1
fi

ln -sfn "$RELEASE_DIR" "$NEXT_DIST_PATH"

if [ -L "$DIST_PATH" ]; then
    mv -Tf "$NEXT_DIST_PATH" "$DIST_PATH"
elif [ -d "$DIST_PATH" ]; then
    LEGACY_DIR="$RELEASES_ROOT/legacy-$(date +%Y%m%d%H%M%S)"
    mv "$DIST_PATH" "$LEGACY_DIR"
    if ! mv -Tf "$NEXT_DIST_PATH" "$DIST_PATH"; then
        rm -f "$NEXT_DIST_PATH"
        mv "$LEGACY_DIR" "$DIST_PATH"
        echo "前端 release 切换失败，已恢复旧 dist" >&2
        exit 1
    fi
elif [ -e "$DIST_PATH" ]; then
    rm -rf "$DIST_PATH"
    mv -Tf "$NEXT_DIST_PATH" "$DIST_PATH"
else
    mv -Tf "$NEXT_DIST_PATH" "$DIST_PATH"
fi
readlink -f "$DIST_PATH"
'''
    result = run_remote_command(command)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def cleanup_old_frontend_releases():
    print("\n=== 清理旧前端 release ===")
    frontend_root = f"{REMOTE_BASE}/frontend"
    releases_root = f"{frontend_root}/.deploy/releases"
    dist_path = f"{frontend_root}/dist"
    command = f'''
set -e
RELEASES_ROOT={remote_shell_quote(releases_root)}
DIST_PATH={remote_shell_quote(dist_path)}
KEEP_COUNT={FRONTEND_RELEASES_KEEP}

if [ ! -d "$RELEASES_ROOT" ]; then
    exit 0
fi

CURRENT_NAME=""
if [ -L "$DIST_PATH" ]; then
    CURRENT_NAME=$(basename "$(readlink "$DIST_PATH")")
fi

RETAIN_OTHERS="$KEEP_COUNT"
if [ -n "$CURRENT_NAME" ] && [ "$KEEP_COUNT" -gt 0 ]; then
    RETAIN_OTHERS=$((KEEP_COUNT - 1))
fi

kept=0

for name in $(find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r); do
    if [ -n "$CURRENT_NAME" ] && [ "$name" = "$CURRENT_NAME" ]; then
        continue
    fi

    if [ "$kept" -lt "$RETAIN_OTHERS" ]; then
        kept=$((kept + 1))
        continue
    fi

    rm -rf "$RELEASES_ROOT/$name"
    echo "Removed old frontend release: $name"
done
'''
    result = run_remote_command(command)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def run_database_migration(release_dir: str):
    print("\n=== 运行数据库迁移 ===")
    command = (
        f"cd {remote_shell_quote(release_dir)} && "
        f"{remote_shell_quote(PYTHON_BIN)} -m alembic upgrade head 2>&1"
    )
    result = run_remote_command(command)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def restart_backend_service():
    print("\n=== 重启后端服务 ===")
    current_backend_dir = f"{REMOTE_BASE}/backend/current"
    pid_file = f"{REMOTE_BASE}/backend/gunicorn.pid"
    restart_script = f'''
set -e
CURRENT_BACKEND={remote_shell_quote(current_backend_dir)}
PID_FILE={remote_shell_quote(pid_file)}
PY={remote_shell_quote(PYTHON_BIN)}
PORT={remote_shell_quote(DEPLOY_BACKEND_PORT)}

if [ ! -d "$CURRENT_BACKEND" ]; then
    echo "后端 current 不存在: $CURRENT_BACKEND" >&2
    exit 1
fi

OLD_PID=""
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
fi

if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
        if ! kill -0 "$OLD_PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    if kill -0 "$OLD_PID" 2>/dev/null; then
        kill -9 "$OLD_PID" 2>/dev/null || true
    fi
fi

get_listen_pid() {{
    ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2 || true
}}

PORT_PID=$(get_listen_pid)
if [ -n "$PORT_PID" ] && kill -0 "$PORT_PID" 2>/dev/null; then
    kill "$PORT_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
        NEXT_PORT_PID=$(get_listen_pid)
        if [ -z "$NEXT_PORT_PID" ]; then
            break
        fi
        sleep 1
    done
    PORT_PID=$(get_listen_pid)
    if [ -n "$PORT_PID" ] && kill -0 "$PORT_PID" 2>/dev/null; then
        kill -9 "$PORT_PID" 2>/dev/null || true
    fi
fi

rm -f "$PID_FILE"

for _ in $(seq 1 20); do
    if [ -z "$(get_listen_pid)" ]; then
        break
    fi
    sleep 1
done

if [ -n "$(get_listen_pid)" ]; then
    echo "端口仍被占用: $PORT" >&2
    exit 1
fi

cd "$CURRENT_BACKEND"
nohup "$PY" -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:$PORT --workers 2 --timeout 300 \
    --pid "$PID_FILE" --access-logfile - --error-logfile - \
    > /var/log/ai_records_gunicorn.log 2>&1 &

for _ in $(seq 1 20); do
    if [ -f "$PID_FILE" ]; then
        break
    fi
    sleep 1
done

PID_VALUE=$(cat "$PID_FILE" 2>/dev/null || true)
if [ -z "$PID_VALUE" ]; then
    echo "gunicorn 未写出 pid 文件: $PID_FILE" >&2
    exit 1
fi

echo "Started gunicorn from $CURRENT_BACKEND (pid=$PID_VALUE)"
'''
    result = run_remote_command(restart_script)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def cleanup_old_backend_releases():
    print("\n=== 清理旧后端 release ===")
    backend_root = f"{REMOTE_BASE}/backend"
    releases_root = f"{backend_root}/.deploy/releases"
    current_path = f"{backend_root}/current"
    command = f'''
set -e
RELEASES_ROOT={remote_shell_quote(releases_root)}
CURRENT_PATH={remote_shell_quote(current_path)}
KEEP_COUNT={BACKEND_RELEASES_KEEP}

if [ ! -d "$RELEASES_ROOT" ]; then
    exit 0
fi

CURRENT_NAME=""
if [ -L "$CURRENT_PATH" ]; then
    CURRENT_NAME=$(basename "$(readlink "$CURRENT_PATH")")
fi

RETAIN_OTHERS="$KEEP_COUNT"
if [ -n "$CURRENT_NAME" ] && [ "$KEEP_COUNT" -gt 0 ]; then
    RETAIN_OTHERS=$((KEEP_COUNT - 1))
fi

kept=0

for name in $(find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r); do
    if [ -n "$CURRENT_NAME" ] && [ "$name" = "$CURRENT_NAME" ]; then
        continue
    fi

    if [ "$kept" -lt "$RETAIN_OTHERS" ]; then
        kept=$((kept + 1))
        continue
    fi

    rm -rf "$RELEASES_ROOT/$name"
    echo "Removed old backend release: $name"
done
'''
    result = run_remote_command(command)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def verify_remote_health():
    print("\n=== 验证部署 ===")
    command = (
        f"curl -s -o /dev/null -w '%{{http_code}}' "
        f"http://127.0.0.1:{DEPLOY_BACKEND_PORT}/api/health"
    )
    result = run_remote_command(command)
    http_code = result.stdout.strip()
    print(f"后端健康检查: HTTP {http_code}")
    if http_code != "200":
        raise RuntimeError(f"远端健康检查失败: HTTP {http_code}")


def main():
    validate_deploy_settings()
    print(f"连接到 {REMOTE_HOST}:{REMOTE_PORT}...")
    print("使用系统 OpenSSH 和 npm 执行部署\n")
    release_id = generate_release_id()
    acquire_remote_deploy_lock()

    try:
        ensure_frontend_build()
        frontend_release_dir = prepare_frontend_release(release_id)
        backend_release_dir = prepare_backend_release(release_id)
        upload_backend_release(backend_release_dir)
        upload_frontend_dist(frontend_release_dir)
        run_database_migration(backend_release_dir)
        previous_backend_target = get_current_symlink_target(f"{REMOTE_BASE}/backend/current")
        activate_backend_release(release_id)
        try:
            restart_backend_service()
            verify_remote_health()
        except Exception:
            if previous_backend_target:
                restore_backend_release(previous_backend_target)
                restart_backend_service()
            raise
        activate_frontend_release(release_id)
        cleanup_old_frontend_releases()
        cleanup_old_backend_releases()
        print("\n部署完成!")
    finally:
        release_remote_deploy_lock()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"部署失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
