"""部署脚本：使用系统 OpenSSH 和 npm，不依赖本地虚拟环境。"""
import argparse
from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
REMOTE_SMOKE_BASE_URL = os.getenv("REMOTE_SMOKE_BASE_URL", "").strip().rstrip("/")
BT_PANEL_PYTHON = os.getenv(
    "DEPLOY_BT_PANEL_PYTHON", "/www/server/panel/pyenv/bin/python"
).strip()
BT_PROJECT_NAME = os.getenv("DEPLOY_BT_PROJECT_NAME", "backend").strip()
BT_RUNTIME_USER = os.getenv("DEPLOY_BT_RUNTIME_USER", "www").strip()
FRONTEND_RELEASES_KEEP = get_int_env("DEPLOY_FRONTEND_RELEASES_KEEP", 4)
BACKEND_RELEASES_KEEP = get_int_env("DEPLOY_BACKEND_RELEASES_KEEP", 4)
SSH_CONNECT_TIMEOUT = get_int_env("DEPLOY_SSH_CONNECT_TIMEOUT", 15)
SSH_SERVER_ALIVE_INTERVAL = get_int_env("DEPLOY_SSH_SERVER_ALIVE_INTERVAL", 15)
SSH_SERVER_ALIVE_COUNT_MAX = get_int_env("DEPLOY_SSH_SERVER_ALIVE_COUNT_MAX", 4)
DEPLOY_LOCK_MAX_MINUTES = get_int_env("DEPLOY_LOCK_MAX_MINUTES", 120)

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
    "browser_data",
    "browser_runtime",
    "static",
    "logs",
}
PERSISTENT_BACKEND_PARTS = {"browser_data", "browser_runtime", "static", "logs"}
BACKEND_RELEASE_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def validate_deploy_settings():
    required_settings = {
        "DEPLOY_REMOTE_HOST": REMOTE_HOST,
        "DEPLOY_REMOTE_USER": REMOTE_USER,
        "DEPLOY_REMOTE_BASE": REMOTE_BASE,
        "DEPLOY_PYTHON_BIN": PYTHON_BIN,
        "DEPLOY_BACKEND_PORT": DEPLOY_BACKEND_PORT,
        "REMOTE_SMOKE_BASE_URL": REMOTE_SMOKE_BASE_URL,
        "DEPLOY_BT_PANEL_PYTHON": BT_PANEL_PYTHON,
        "DEPLOY_BT_PROJECT_NAME": BT_PROJECT_NAME,
        "DEPLOY_BT_RUNTIME_USER": BT_RUNTIME_USER,
    }
    missing_settings = [name for name, value in required_settings.items() if not value]
    if missing_settings:
        raise RuntimeError("缺少部署环境变量: " + ", ".join(missing_settings))
    missing_persistent_parts = sorted(PERSISTENT_BACKEND_PARTS - BACKEND_RELEASE_EXCLUDED_PARTS)
    if missing_persistent_parts:
        raise RuntimeError(
            "部署保护缺失：以下后端持久化目录必须排除在 release 上传和清理之外: "
            + ", ".join(missing_persistent_parts)
        )
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
    args = [
        SSH_BIN,
        "-p",
        str(REMOTE_PORT),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        "-o",
        f"ServerAliveInterval={SSH_SERVER_ALIVE_INTERVAL}",
        "-o",
        f"ServerAliveCountMax={SSH_SERVER_ALIVE_COUNT_MAX}",
    ]
    if REMOTE_KEY_PATH:
        args.extend(["-i", REMOTE_KEY_PATH, "-o", "BatchMode=yes"])
    args.append(remote_login())
    return args


def scp_base_args() -> list[str]:
    args = [
        SCP_BIN,
        "-P",
        str(REMOTE_PORT),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        "-o",
        f"ServerAliveInterval={SSH_SERVER_ALIVE_INTERVAL}",
        "-o",
        f"ServerAliveCountMax={SSH_SERVER_ALIVE_COUNT_MAX}",
    ]
    if REMOTE_KEY_PATH:
        args.extend(["-i", REMOTE_KEY_PATH, "-o", "BatchMode=yes"])
    return args


def run_remote_command(command: str, check: bool = True):
    return run_captured_command(
        ssh_base_args() + [f"bash -lc {shlex.quote(command)}"],
        check=check,
    )


def run_remote_streaming_command(command: str):
    run_streaming_command(
        ssh_base_args() + [f"bash -lc {shlex.quote(command)}"],
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


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="发布当前项目到远端服务器")
    parser.add_argument(
        "--force-unlock",
        action="store_true",
        help="获取部署锁前先强制清理已有锁目录",
    )
    parser.add_argument(
        "--unlock-only",
        action="store_true",
        help="仅清理远端部署锁，不执行构建和部署",
    )
    parser.add_argument(
        "--lock-max-minutes",
        type=int,
        default=DEPLOY_LOCK_MAX_MINUTES,
        help=f"部署锁超过多少分钟视为残留锁并自动回收，默认 {DEPLOY_LOCK_MAX_MINUTES} 分钟",
    )
    return parser.parse_args()


def acquire_remote_deploy_lock(*, force_unlock: bool = False, lock_max_minutes: int = DEPLOY_LOCK_MAX_MINUTES):
    print("\n=== 获取远端部署锁 ===")
    lock_root = f"{REMOTE_BASE}/.deploy"
    lock_dir = f"{lock_root}/deploy.lock"
    max_age_seconds = max(60, int(lock_max_minutes) * 60)
    command = f'''
set -e
LOCK_ROOT={remote_shell_quote(lock_root)}
LOCK_DIR={remote_shell_quote(lock_dir)}
FORCE_UNLOCK={"1" if force_unlock else "0"}
MAX_AGE_SECONDS={max_age_seconds}
ACQUIRED_AT={remote_shell_quote(datetime.now(timezone.utc).isoformat())}

mkdir -p "$LOCK_ROOT"

write_lock() {{
    printf '%s\n' "$ACQUIRED_AT" > "$LOCK_DIR/acquired_at"
}}

if mkdir "$LOCK_DIR" 2>/dev/null; then
    write_lock
    echo "已获取部署锁"
    exit 0
fi

LOCK_MTIME=$(stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0)
NOW_TS=$(date +%s)
LOCK_AGE=$((NOW_TS - LOCK_MTIME))
if [ "$LOCK_AGE" -lt 0 ]; then
    LOCK_AGE=0
fi

if [ "$FORCE_UNLOCK" = "1" ]; then
    echo "强制清理已有部署锁: $LOCK_DIR"
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR"
    write_lock
    echo "已重新获取部署锁"
    exit 0
fi

if [ "$LOCK_AGE" -ge "$MAX_AGE_SECONDS" ]; then
    echo "发现残留部署锁（已存在 ${{LOCK_AGE}}s），自动回收: $LOCK_DIR"
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR"
    write_lock
    echo "已重新获取部署锁"
    exit 0
fi

    echo "已有部署任务在运行，请稍后重试或使用 --force-unlock: $LOCK_DIR" >&2
    if [ -f "$LOCK_DIR/acquired_at" ]; then
        echo "锁创建时间: $(cat "$LOCK_DIR/acquired_at")" >&2
    fi
    echo "当前锁年龄: ${{LOCK_AGE}}s" >&2
    exit 1
'''
    result = run_remote_command(command)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def unlock_remote_deploy_lock():
    print("\n=== 清理远端部署锁 ===")
    lock_dir = f"{REMOTE_BASE}/.deploy/deploy.lock"
    command = f'''
set -e
LOCK_DIR={remote_shell_quote(lock_dir)}

if [ -d "$LOCK_DIR" ]; then
    rm -rf "$LOCK_DIR"
    echo "已清理部署锁: $LOCK_DIR"
else
    echo "部署锁不存在: $LOCK_DIR"
fi
'''
    result = run_remote_command(command, check=False)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


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


def collect_upload_files(
    local_dir: Path,
    *,
    file_filter: Callable[[Path], bool] | None = None,
) -> list[Path]:
    return sorted(
        path
        for path in local_dir.rglob("*")
        if path.is_file() and (file_filter(path) if file_filter else True)
    )


def create_upload_archive(
    local_dir: Path,
    *,
    file_filter: Callable[[Path], bool] | None = None,
) -> tuple[Path, int]:
    files = collect_upload_files(local_dir, file_filter=file_filter)
    archive_handle = tempfile.NamedTemporaryFile(
        prefix=f"{local_dir.name}-deploy-",
        suffix=".tar",
        delete=False,
    )
    archive_path = Path(archive_handle.name)
    archive_handle.close()

    try:
        with tarfile.open(archive_path, "w") as archive:
            for local_path in files:
                archive.add(
                    local_path,
                    arcname=local_path.relative_to(local_dir).as_posix(),
                    recursive=False,
                )
        return archive_path, len(files)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


def upload_dir_as_archive(
    local_dir: Path,
    remote_dir: str,
    *,
    file_filter: Callable[[Path], bool] | None = None,
):
    archive_path, file_count = create_upload_archive(local_dir, file_filter=file_filter)
    remote_archive_path = f"{remote_dir}/.__deploy_bundle__.tar"
    try:
        print(f"  打包 {local_dir.name}：{file_count} 个文件")
        upload_file(archive_path, remote_archive_path)
        run_remote_command(
            f"mkdir -p {remote_shell_quote(remote_dir)} && "
            f"tar -xf {remote_shell_quote(remote_archive_path)} -C {remote_shell_quote(remote_dir)} && "
            f"rm -f {remote_shell_quote(remote_archive_path)}"
        )
        print(f"  [OK] 已解包到 {remote_dir}（{file_count} 个文件）")
    finally:
        archive_path.unlink(missing_ok=True)


def prepare_backend_release(release_id: str) -> str:
    print("\n=== 准备后端 release ===")
    backend_root = f"{REMOTE_BASE}/backend"
    releases_root = f"{backend_root}/.deploy/releases"
    release_dir = f"{releases_root}/{release_id}"
    # backend/static 和 backend/logs 是持久化目录，只允许确保存在，不能随 release 切换清理。
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
    upload_dir_as_archive(BACKEND_DIR, release_dir, file_filter=should_include_backend_release_file)


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

# 历史上 backend/current 曾是实体目录而不是 symlink。直接删掉会连带丢失里面的
# browser_data（浏览器登录会话）和可能残留的抓取媒体，所以先抢救再改名保留。
if [ -e "$SYMLINK_PATH" ] && [ ! -L "$SYMLINK_PATH" ]; then
    if [ ! -d "$SYMLINK_PATH" ]; then
        echo "目标路径已存在且既不是 symlink 也不是目录: $SYMLINK_PATH" >&2
        exit 1
    fi

    PARENT_DIR=$(dirname "$SYMLINK_PATH")
    MEDIA_DIRS=$(find "$SYMLINK_PATH" -type d \\( -name 'qq_images' -o -name 'xhs_images' \\
        -o -name 'avatars' -o -name 'qq_videos' -o -name 'xhs_videos' \\) 2>/dev/null | wc -l)
    MEDIA_FILES=0
    if [ "$MEDIA_DIRS" -gt 0 ]; then
        MEDIA_FILES=$(find "$SYMLINK_PATH" -type f -path '*/static/*' 2>/dev/null | wc -l)
    fi
    if [ "$MEDIA_FILES" -gt 0 ]; then
        echo "拒绝改动：$SYMLINK_PATH 内仍有 $MEDIA_FILES 个媒体文件，请先手动抢救" >&2
        exit 1
    fi

    if [ -d "$SYMLINK_PATH/browser_data" ]; then
        echo "保留浏览器登录态到 $PARENT_DIR/browser_data"
        mkdir -p "$PARENT_DIR/browser_data"
        cp -a "$SYMLINK_PATH/browser_data/." "$PARENT_DIR/browser_data/"
    fi

    PRESERVED="$SYMLINK_PATH.pre-symlink-$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$SYMLINK_PATH" "$PRESERVED"
    echo "原目录已改名保留: $PRESERVED"
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
    upload_dir_as_archive(FRONTEND_DIST, release_dir)


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


def install_backend_dependencies(release_dir: str):
    print("\n=== 安装后端依赖 ===")
    command = (
        f"cd {remote_shell_quote(release_dir)} && "
        f"{remote_shell_quote(PYTHON_BIN)} -m pip install -r requirements.txt"
    )
    print("  远端执行 pip install -r requirements.txt")
    run_remote_streaming_command(command)


def ensure_browser_runtime():
    """安装两套引擎共用的持久化浏览器运行时。"""

    print("\n=== 检查浏览器运行时 ===")
    browser_runtime_dir = f"{REMOTE_BASE}/backend/browser_runtime"
    browser_runtime_alias = "/www/aib"
    script = f'''
set -euo pipefail
PY={remote_shell_quote(PYTHON_BIN)}
RUNTIME_USER={remote_shell_quote(BT_RUNTIME_USER)}
RUNTIME_HOME=$(getent passwd "$RUNTIME_USER" | cut -d: -f6)
BROWSER_DATA={remote_shell_quote(f"{REMOTE_BASE}/backend/browser_data")}
BROWSER_RUNTIME={remote_shell_quote(browser_runtime_dir)}
BROWSER_ALIAS={remote_shell_quote(browser_runtime_alias)}
DRIVER_NODE=$("$PY" -c 'from pathlib import Path; import patchright; print(Path(patchright.__file__).parent / "driver" / "node")')

test -n "$RUNTIME_HOME"
test -d "$RUNTIME_HOME"
command -v runuser >/dev/null
install -d -m 755 "$BROWSER_RUNTIME"
if [ -e "$BROWSER_ALIAS" ] && [ ! -L "$BROWSER_ALIAS" ]; then
    echo "浏览器短路径已被非软链接文件占用: $BROWSER_ALIAS" >&2
    exit 1
fi
ln -sfn "$BROWSER_RUNTIME" "$BROWSER_ALIAS"
ln -sfn "$DRIVER_NODE" "$BROWSER_ALIAS/node"

if [ -d "$BROWSER_DATA" ]; then
    chown -R "$RUNTIME_USER:$RUNTIME_USER" "$BROWSER_DATA"
fi

if command -v google-chrome >/dev/null 2>&1; then
    google-chrome --version
elif command -v google-chrome-stable >/dev/null 2>&1; then
    google-chrome-stable --version
else
    echo "系统 Chrome 未安装，使用共享 bundled Chromium"
fi

env PLAYWRIGHT_BROWSERS_PATH="$BROWSER_RUNTIME" "$PY" -m patchright install chromium 2>&1 | tail -3
env PLAYWRIGHT_BROWSERS_PATH="$BROWSER_RUNTIME" "$PY" -m playwright install chromium 2>&1 | tail -3
chmod -R a+rX "$BROWSER_RUNTIME"
'''
    run_remote_streaming_command(script)


def verify_browser_runtime():
    """以宝塔运行用户真实启动 patchright 与 playwright。"""
    print("\n=== 验证宝塔浏览器运行时 ===")
    current_backend_dir = f"{REMOTE_BASE}/backend/current"
    browser_runtime_dir = "/www/aib"
    script = f'''
set -euo pipefail
PY={remote_shell_quote(PYTHON_BIN)}
RUNTIME_USER={remote_shell_quote(BT_RUNTIME_USER)}
RUNTIME_HOME=$(getent passwd "$RUNTIME_USER" | cut -d: -f6)
BROWSER_RUNTIME={remote_shell_quote(browser_runtime_dir)}
DRIVER_NODE="$BROWSER_RUNTIME/node"

cd {remote_shell_quote(current_backend_dir)}
runuser -u "$RUNTIME_USER" -- env \
    HOME="$RUNTIME_HOME" \
    PLAYWRIGHT_BROWSERS_PATH="$BROWSER_RUNTIME" \
    PLAYWRIGHT_NODEJS_PATH="$DRIVER_NODE" \
    timeout 180 "$PY" - <<'PYEOF'
import asyncio
import os
import pwd


async def main():
    from patchright.async_api import async_playwright as patchright_playwright
    from playwright.async_api import async_playwright as playwright_playwright

    for name, factory in (
        ("patchright", patchright_playwright),
        ("playwright", playwright_playwright),
    ):
        driver = await factory().start()
        try:
            browser = await driver.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            await browser.close()
            user = pwd.getpwuid(os.getuid()).pw_name
            print(f"浏览器自检通过: {{name}} user={{user}}")
        finally:
            await driver.stop()


asyncio.run(main())
PYEOF
'''
    run_remote_streaming_command(script)


def ensure_baota_security_whitelist():
    """允许宝塔运行用户在本项目 release 目录启动受管服务与浏览器。"""
    print("\n=== 配置宝塔项目安全白名单 ===")
    releases_root = f"{REMOTE_BASE}/backend/.deploy/releases"
    backup_dir = f"{REMOTE_BASE}/backups"
    current_backend_dir = f"{REMOTE_BASE}/backend/current"
    browser_runtime_alias = "/www/aib"
    script = f'''
set -euo pipefail
CONFIG=/usr/local/usranalyse/etc/usranalyse.ini
BACKUP_DIR={remote_shell_quote(backup_dir)}
RUNTIME_USER={remote_shell_quote(BT_RUNTIME_USER)}
SECURITY_LOGIN="$RUNTIME_USER"
RELEASES_ROOT={remote_shell_quote(releases_root)}
CURRENT_RELEASE=$(readlink -f {remote_shell_quote(current_backend_dir)})
BROWSER_RUNTIME={remote_shell_quote(browser_runtime_alias)}

case "$CURRENT_RELEASE" in
    "$RELEASES_ROOT"/release-*) ;;
    *) echo "current 未指向合法 release，拒绝写入安全白名单" >&2; exit 1 ;;
esac

NODE="$BROWSER_RUNTIME/node"
CHROME=$(find -L "$BROWSER_RUNTIME" -type f -path '*/chrome-linux/chrome' -perm /111 | sort | tail -1)
HEADLESS_SHELL=$(find -L "$BROWSER_RUNTIME" -type f -name headless_shell -perm /111 | sort | tail -1)

test -x "$NODE"
test -x "$CHROME"
test -x "$HEADLESS_SHELL"

# 宝塔由 root 登录会话降权启动 www 进程。安全日志同时记录 login=root 和 uid=www，
# 但白名单首字段按实际进程 uid 匹配，因此使用项目运行用户。
RULES="stop_pwd:$SECURITY_LOGIN,$RELEASES_ROOT,nohup;stop_pwd:$SECURITY_LOGIN,$RELEASES_ROOT,$NODE;stop_pwd:$SECURITY_LOGIN,$RELEASES_ROOT,$CHROME;stop_pwd:$SECURITY_LOGIN,$RELEASES_ROOT,$HEADLESS_SHELL;stop_pwd:$SECURITY_LOGIN,$RELEASES_ROOT,run-driver;stop_pwd:$SECURITY_LOGIN,$RELEASES_ROOT,--remote-debugging-pipe"

test -f "$CONFIG"
mkdir -p "$BACKUP_DIR"
python3 - "$CONFIG" "$BACKUP_DIR" "$RULES" "$RUNTIME_USER" "$SECURITY_LOGIN" "$RELEASES_ROOT" "$CURRENT_RELEASE" <<'PYEOF'
import datetime
import os
import re
import shutil
import stat
import sys
import tempfile

(
    config_path,
    backup_dir,
    rule_blob,
    runtime_user,
    security_login,
    releases_root,
    current_release,
) = sys.argv[1:]
requested_rules = [item for item in rule_blob.split(";") if item]
managed_commands = {{
    "nohup",
    "node",
    "chrome",
    "headless_shell",
    "run-driver",
    "--remote-debugging-pipe",
}}
with open(config_path, "r", encoding="utf-8") as config_file:
    original = config_file.read()

match = re.search(
    r'(?m)^(whitepwdstop_chain\\s*=\\s*")([^"]*)("[^\\r\\n]*)$',
    original,
)
if not match:
    raise SystemExit("未找到 whitepwdstop_chain，拒绝修改未知格式的安全配置")

rules = [item.strip() for item in match.group(2).split(";") if item.strip()]
def is_managed(rule: str) -> bool:
    parts = rule.split(",", 2)
    if len(parts) != 3 or parts[0] not in {{
        f"stop_pwd:{{runtime_user}}",
        f"stop_pwd:{{security_login}}",
    }}:
        return False
    path, command = parts[1:]
    if path != releases_root and not path.startswith(releases_root + "/release-"):
        return False
    return os.path.basename(command) in managed_commands


filtered_rules = [rule for rule in rules if not is_managed(rule)]
removed_count = len(rules) - len(filtered_rules)
filtered_rules = requested_rules + filtered_rules
if rules == filtered_rules:
    print("宝塔安全白名单已存在")
    raise SystemExit(0)

timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_path = os.path.join(backup_dir, f"usranalyse-before-arq-{{timestamp}}.ini")
shutil.copy2(config_path, backup_path)
os.chmod(backup_path, stat.S_IRUSR | stat.S_IWUSR)

updated = original[:match.start(2)] + ";".join(filtered_rules) + original[match.end(2):]
config_dir = os.path.dirname(config_path)
fd, temp_path = tempfile.mkstemp(prefix=".usranalyse.", dir=config_dir, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as temp_file:
        temp_file.write(updated)
        temp_file.flush()
        os.fsync(temp_file.fileno())
    shutil.copystat(config_path, temp_path)
    os.replace(temp_path, config_path)
finally:
    if os.path.exists(temp_path):
        os.unlink(temp_path)

with open(config_path, "r", encoding="utf-8") as config_file:
    current = config_file.read()
if any(rule not in current for rule in requested_rules):
    raise SystemExit("白名单写入后校验失败")
current_match = re.search(
    r'(?m)^(whitepwdstop_chain\\s*=\\s*")([^"]*)("[^\\r\\n]*)$',
    current,
)
if not current_match:
    raise SystemExit("白名单写入后格式校验失败")
current_rules = [item.strip() for item in current_match.group(2).split(";") if item.strip()]
if any(
    is_managed(rule) and rule.split(",", 2)[1] != releases_root
    for rule in current_rules
):
    raise SystemExit("旧 release 白名单未清除")
print(
    f"宝塔安全白名单已写入并校验：项目级可执行文件 4 条、固定参数 2 条，"
    f"移除无效规则 {{removed_count}} 条"
)
PYEOF
'''
    result = run_remote_command(script)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def configure_baota_python_project():
    """让宝塔 Python 项目管理器负责后端主服务和 arq 协同服务。"""
    print("\n=== 配置宝塔 Python 项目 ===")
    current_backend_dir = f"{REMOTE_BASE}/backend/current"
    env_file = f"{REMOTE_BASE}/.env"
    backup_dir = f"{REMOTE_BASE}/backups"
    browser_runtime_dir = "/www/aib"
    browser_node_path = "/www/aib/node"
    script = f'''
set -euo pipefail
PANEL_PY={remote_shell_quote(BT_PANEL_PYTHON)}
CURRENT_BACKEND={remote_shell_quote(current_backend_dir)}
ENV_FILE={remote_shell_quote(env_file)}
BACKUP_DIR={remote_shell_quote(backup_dir)}
PROJECT_NAME={remote_shell_quote(BT_PROJECT_NAME)}
APP_PY={remote_shell_quote(PYTHON_BIN)}
PORT={remote_shell_quote(DEPLOY_BACKEND_PORT)}
RUNTIME_USER={remote_shell_quote(BT_RUNTIME_USER)}
BROWSER_RUNTIME={remote_shell_quote(browser_runtime_dir)}
BROWSER_NODE={remote_shell_quote(browser_node_path)}

test -x "$PANEL_PY"
test -d "$CURRENT_BACKEND"
test -f "$CURRENT_BACKEND/app/main.py"
test -f "$ENV_FILE"
mkdir -p "$BACKUP_DIR"

PANEL_SCRIPT=$(mktemp)
trap 'rm -f "$PANEL_SCRIPT"' EXIT
cat > "$PANEL_SCRIPT" <<'PYEOF'
import datetime
import json
import os
import sys
from uuid import uuid4

sys.path.insert(0, "/www/server/panel")
sys.path.insert(0, "/www/server/panel/class")
import public
from projectModel.pythonModel import main as PythonProjectManager

(
    project_name,
    current_backend,
    env_file,
    backup_dir,
    app_python,
    port,
    runtime_user,
    browser_runtime,
    browser_node,
) = sys.argv[1:]
record = public.M("sites").where(
    "project_type=? AND name=?", ("Python", project_name)
).field("id,name,path,status,project_config").find()
if not record:
    raise SystemExit("宝塔中不存在指定的 Python 项目")

config = json.loads(record["project_config"])
timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_path = os.path.join(backup_dir, f"bt-python-{{project_name}}-before-{{timestamp}}.json")
with open(backup_path, "x", encoding="utf-8") as backup_file:
    json.dump(record, backup_file, ensure_ascii=False, indent=2)
os.chmod(backup_path, 0o600)

registered_python = os.path.join(config.get("vpath", ""), "bin", "python")
if not os.path.isfile(registered_python):
    registered_python = app_python
arq_command = f"{{registered_python}} -m arq app.crawl.worker.WorkerSettings"
services = []
arq_service = None
for service in config.get("services") or []:
    if (
        service.get("name") == "arq-worker"
        or "app.crawl.worker.WorkerSettings" in service.get("command", "")
    ):
        if arq_service is None:
            arq_service = service
        continue
    services.append(service)
if arq_service is None:
    arq_service = {{"sid": uuid4().hex[::3]}}
arq_service.update({{
    "name": "arq-worker",
    "command": arq_command,
    "level": 20,
    "log_type": "append",
}})
services.append(arq_service)
env_list = [
    item for item in (config.get("env_list") or [])
    if item.get("k") not in {{"PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_NODEJS_PATH"}}
]
env_list.append({{"k": "PLAYWRIGHT_BROWSERS_PATH", "v": browser_runtime}})
env_list.append({{"k": "PLAYWRIGHT_NODEJS_PATH", "v": browser_node}})

config.update({{
    "pjname": project_name,
    "path": current_backend,
    "rfile": os.path.join(current_backend, "app", "main.py"),
    "python_bin": registered_python,
    "stype": "gunicorn",
    "xsgi": "asgi",
    "call_app": "app",
    "port": port,
    "processes": 1,
    "threads": 2,
    "user": runtime_user,
    "auto_run": True,
    "env_file": env_file,
    "env_list": env_list,
    "services": services,
}})
public.M("sites").where("id=?", (record["id"],)).update({{
    "path": current_backend,
    "project_config": json.dumps(config, ensure_ascii=False),
}})
python_project = PythonProjectManager()
generated_env_file = os.path.join(python_project._env_path, f"{{project_name}}.env")
python_project._build_env_file(generated_env_file, config)
print("宝塔项目已更新：current 路径、单进程、开机启动、arq 协同服务")
PYEOF

"$PANEL_PY" "$PANEL_SCRIPT" "$PROJECT_NAME" "$CURRENT_BACKEND" "$ENV_FILE" "$BACKUP_DIR" "$APP_PY" "$PORT" "$RUNTIME_USER" "$BROWSER_RUNTIME" "$BROWSER_NODE"
'''
    result = run_remote_command(script)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def restart_arq_worker():
    """Redis 可用时通过宝塔协同服务启动 arq，并停用旧 systemd 单元。"""
    print("\n=== 重启采集 worker ===")
    current_backend_dir = f"{REMOTE_BASE}/backend/current"
    restart_script = f'''
set +e
CURRENT_BACKEND={remote_shell_quote(current_backend_dir)}
PY={remote_shell_quote(PYTHON_BIN)}
PANEL_PY={remote_shell_quote(BT_PANEL_PYTHON)}
PROJECT_NAME={remote_shell_quote(BT_PROJECT_NAME)}
RUNTIME_USER={remote_shell_quote(BT_RUNTIME_USER)}

if [ ! -d "$CURRENT_BACKEND" ]; then
    echo "后端 current 不存在，跳过 arq worker"
    exit 0
fi

stop_baota_arq() {{
    "$PANEL_PY" - "$PROJECT_NAME" <<'PYSTOP'
import sys

sys.path.insert(0, "/www/server/panel")
sys.path.insert(0, "/www/server/panel/class")
from mod.project.python.serviceMod import ServiceManager

manager = ServiceManager.new_mgr(sys.argv[1])
if isinstance(manager, str):
    raise SystemExit(manager)
service = next(
    (
        item for item in manager.other_services
        if item.get("name") == "arq-worker"
        and "app.crawl.worker.WorkerSettings" in item.get("command", "")
    ),
    None,
)
if service is not None:
    error = manager.handle_service(service["sid"], "stop")
    if error:
        raise SystemExit(error)
PYSTOP
}}

cd "$CURRENT_BACKEND"

# 必须按应用实际配置探测 Redis。裸跑 redis-cli 会忽略 REDIS_URL 中的密码，且 NOAUTH
# 仍可能返回成功退出码，无法判断 worker 是否真的可用。探测只回传状态/异常类型，不输出 URL。
REDIS_CHECK=$(timeout 15 "$PY" - <<'PYEOF' 2>&1
import asyncio

import redis.asyncio as redis_asyncio

from app.core.config import settings


async def main():
    if not settings.redis_enabled:
        print("DISABLED")
        return
    client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
    try:
        pong = await asyncio.wait_for(client.ping(), timeout=5)
        print("PONG" if pong else "ERROR:UnexpectedPingResponse")
    except Exception as exc:
        print("ERROR:" + type(exc).__name__)
    finally:
        await client.aclose()


asyncio.run(main())
PYEOF
)
case "$REDIS_CHECK" in
    PONG) ;;
    DISABLED)
        echo "Redis 已禁用，跳过 arq worker（API 进程会后台执行采集）"
        stop_baota_arq || true
        systemctl disable --now ai-records-arq.service >/dev/null 2>&1 || true
        exit 0 ;;
    *)
        echo "Redis 配置探测失败（$REDIS_CHECK），跳过 arq worker（API 进程会后台执行采集）"
        stop_baota_arq || true
        systemctl disable --now ai-records-arq.service >/dev/null 2>&1 || true
        exit 0 ;;
esac

PANEL_SCRIPT=$(mktemp)
trap 'rm -f "$PANEL_SCRIPT"' EXIT
cat > "$PANEL_SCRIPT" <<'PYEOF'
import os
import pwd
import sys
import time

sys.path.insert(0, "/www/server/panel")
sys.path.insert(0, "/www/server/panel/class")
from mod.project.python.serviceMod import ServiceManager

mode, project_name, runtime_user = sys.argv[1:4]
manager = ServiceManager.new_mgr(project_name)
if isinstance(manager, str):
    raise SystemExit(manager)
service = next(
    (
        item for item in manager.other_services
        if item.get("name") == "arq-worker"
        and "app.crawl.worker.WorkerSettings" in item.get("command", "")
    ),
    None,
)
if service is None:
    raise SystemExit("宝塔 arq 协同服务不存在")
if mode == "restart":
    error = manager.handle_service(service["sid"], "restart")
    if error:
        raise SystemExit(error)
elif mode == "inspect":
    for _ in range(30):
        info = next(
            (item for item in manager.get_services_info() if item.get("sid") == service["sid"]),
            {{}},
        )
        pid = info.get("pid")
        if pid:
            try:
                stat_fields = open(f"/proc/{{pid}}/stat", encoding="utf-8").read().split()
                if stat_fields[2] == "Z":
                    pid = None
            except OSError:
                pid = None
        if pid:
            owner = pwd.getpwuid(os.stat(f"/proc/{{pid}}").st_uid).pw_name
            if owner != runtime_user:
                raise SystemExit(f"arq 协同服务用户错误: {{owner}}")
            print(f"宝塔 arq 协同服务已启动 (pid={{pid}}, user={{owner}})")
            raise SystemExit(0)
        time.sleep(1)
    raise SystemExit("宝塔 arq 协同服务未能常驻")
else:
    raise SystemExit("未知操作")
PYEOF

# 先停止旧进程，避免同一队列在两个管理器下重复消费；迁移失败会立即恢复 systemd。
systemctl stop ai-records-arq.service >/dev/null 2>&1 || true
if ! "$PANEL_PY" "$PANEL_SCRIPT" restart "$PROJECT_NAME" "$RUNTIME_USER" \
    || ! "$PANEL_PY" "$PANEL_SCRIPT" inspect "$PROJECT_NAME" "$RUNTIME_USER"; then
    echo "宝塔 arq 启动失败，恢复 systemd 服务" >&2
    systemctl enable --now ai-records-arq.service >/dev/null 2>&1 || true
    exit 1
fi
systemctl disable ai-records-arq.service >/dev/null 2>&1 || true
echo "原 ai-records-arq.service 已停用"
'''
    result = run_remote_command(restart_script)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def verify_baota_browser_environment():
    """确认宝塔主服务及其子进程实际继承浏览器环境变量。"""
    script = f'''
set -euo pipefail
{remote_shell_quote(BT_PANEL_PYTHON)} - {remote_shell_quote(BT_PROJECT_NAME)} <<'PYEOF'
import os
import sys

import psutil

sys.path.insert(0, "/www/server/panel")
sys.path.insert(0, "/www/server/panel/class")
from mod.project.python.serviceMod import ServiceManager

manager = ServiceManager.new_mgr(sys.argv[1])
if isinstance(manager, str):
    raise SystemExit(manager)
info = next((item for item in manager.get_services_info() if item.get("sid") == "main"), {{}})
main_pid = info.get("pid")
if not main_pid:
    raise SystemExit("宝塔后端主服务没有运行")

expected = {{
    "PLAYWRIGHT_BROWSERS_PATH": "/www/aib",
    "PLAYWRIGHT_NODEJS_PATH": "/www/aib/node",
}}
processes = [psutil.Process(main_pid)]
processes.extend(processes[0].children(recursive=True))
checked = 0
for process in processes:
    try:
        environ = process.environ()
    except (psutil.Error, OSError):
        continue
    if not any("gunicorn" in part or "uvicorn" in part for part in process.cmdline()):
        continue
    checked += 1
    for key, value in expected.items():
        actual = environ.get(key)
        if actual != value:
            raise SystemExit(
                f"宝塔进程环境变量错误: pid={{process.pid}} {{key}}={{actual!r}}"
            )
print(f"宝塔浏览器环境已生效: {{checked}} 个后端进程")
PYEOF
'''
    result = run_remote_command(script)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def run_database_migration(release_dir: str):
    print("\n=== 运行数据库迁移 ===")
    command = (
        f"cd {remote_shell_quote(release_dir)} && "
        f"{remote_shell_quote(PYTHON_BIN)} -m alembic upgrade head 2>&1"
    )
    print("  远端执行 alembic upgrade head")
    run_remote_streaming_command(command)


def restart_backend_service():
    print("\n=== 通过宝塔重启后端服务 ===")
    current_backend_dir = f"{REMOTE_BASE}/backend/current"
    restart_script = f'''
set -e
CURRENT_BACKEND={remote_shell_quote(current_backend_dir)}
PORT={remote_shell_quote(DEPLOY_BACKEND_PORT)}
PANEL_PY={remote_shell_quote(BT_PANEL_PYTHON)}
PROJECT_NAME={remote_shell_quote(BT_PROJECT_NAME)}

if [ ! -d "$CURRENT_BACKEND" ]; then
    echo "后端 current 不存在: $CURRENT_BACKEND" >&2
    exit 1
fi

systemctl disable --now ai-records-backend.service >/dev/null 2>&1 || true

PANEL_SCRIPT=$(mktemp)
trap 'rm -f "$PANEL_SCRIPT"' EXIT
cat > "$PANEL_SCRIPT" <<'PYEOF'
import json
import os
import shlex
import sys
import time

sys.path.insert(0, "/www/server/panel")
sys.path.insert(0, "/www/server/panel/class")
import public
import panelTask
from mod.project.python.serviceMod import ServiceManager

mode, project_name = sys.argv[1:3]
manager = ServiceManager.new_mgr(project_name)
if isinstance(manager, str):
    raise SystemExit(manager)
if mode == "rollback":
    manager.stop_project()
    record = public.M("sites").where(
        "project_type=? AND name=?", ("Python", project_name)
    ).field("id,project_config").find()
    config = json.loads(record["project_config"])
    config["auto_run"] = False
    public.M("sites").where("id=?", (record["id"],)).update({{
        "project_config": json.dumps(config, ensure_ascii=False),
    }})
    print("宝塔项目已停止并关闭自动启动")
    raise SystemExit(0)
if mode == "queue":
    record = public.M("sites").where(
        "project_type=? AND name=?", ("Python", project_name)
    ).field("id,project_config").find()
    config = json.loads(record["project_config"])
    if not config.get("auto_run"):
        config["auto_run"] = True
        public.M("sites").where("id=?", (record["id"],)).update({{
            "project_config": json.dumps(config, ensure_ascii=False),
        }})
    command = " ".join(
        shlex.quote(part)
        for part in [sys.executable, os.path.abspath(__file__), "worker", project_name]
    )
    task_id = panelTask.bt_task().create_task(
        f"重启 Python 项目 {{project_name}} 主服务", 0, command
    )
    print(task_id)
elif mode == "worker":
    error = manager.handle_service("main", "restart")
    if error:
        raise SystemExit(error)
elif mode == "status":
    task_id = int(sys.argv[3])
    status = public.M("task_list").where("id=?", (task_id,)).getField("status")
    print(status)
elif mode == "inspect":
    time.sleep(2)
    info = next((item for item in manager.get_services_info() if item.get("sid") == "main"), {{}})
    if not info.get("pid"):
        raise SystemExit("宝塔后端主服务未能启动")
    print(f"宝塔后端主服务已启动 (pid={{info['pid']}})")
else:
    raise SystemExit("未知的宝塔任务操作")
PYEOF

TASK_ID=$("$PANEL_PY" "$PANEL_SCRIPT" queue "$PROJECT_NAME" | tail -n 1)
case "$TASK_ID" in
    ''|*[!0-9]*) echo "宝塔任务创建失败" >&2; TASK_ID="" ;;
esac
TASK_STATE=""
if [ -n "$TASK_ID" ]; then
    for _ in $(seq 1 60); do
        TASK_STATE=$("$PANEL_PY" "$PANEL_SCRIPT" status "$PROJECT_NAME" "$TASK_ID" | tail -n 1)
        [ "$TASK_STATE" = "1" ] && break
        sleep 1
    done
fi
if [ "$TASK_STATE" != "1" ] || ! "$PANEL_PY" "$PANEL_SCRIPT" inspect "$PROJECT_NAME"; then
    echo "宝塔启动失败，恢复 systemd 服务" >&2
    "$PANEL_PY" "$PANEL_SCRIPT" rollback "$PROJECT_NAME" >/dev/null 2>&1 || true
    systemctl enable --now ai-records-backend.service >/dev/null 2>&1 || true
    exit 1
fi

for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
        exit 0
    fi
    sleep 1
done

echo "宝塔后端启动后健康检查超时，恢复 systemd 服务" >&2
"$PANEL_PY" "$PANEL_SCRIPT" rollback "$PROJECT_NAME" >/dev/null 2>&1 || true
systemctl enable --now ai-records-backend.service >/dev/null 2>&1 || true
exit 1
'''
    result = run_remote_command(restart_script)
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output[:600])


def cleanup_old_backend_releases():
    """清理旧 release。

    历史上 STATIC_DIR 曾是相对路径，导致抓取到的媒体落进 release 目录，被此处的清理逻辑
    连带删除。这些资源不可再生，因此删除前必须先把 release 内残留的媒体复制进持久化
    backend/static，复制失败就跳过删除而不是继续。
    """
    print("\n=== 清理旧后端 release ===")
    backend_root = f"{REMOTE_BASE}/backend"
    releases_root = f"{backend_root}/.deploy/releases"
    current_path = f"{backend_root}/current"
    persist_static = f"{backend_root}/static"
    command = f'''
set -e
RELEASES_ROOT={remote_shell_quote(releases_root)}
CURRENT_PATH={remote_shell_quote(current_path)}
PERSIST_STATIC={remote_shell_quote(persist_static)}
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

# 删除前抢救 release 里残留的抓取媒体；返回 1 表示有文件没救出来，此时不得删除该 release。
rescue_release_media() {{
    release_dir="$1"
    src_root="$release_dir/static"
    [ -d "$src_root" ] || return 0

    pending=$(find "$src_root" -type f 2>/dev/null | wc -l)
    [ "$pending" -eq 0 ] && return 0

    echo "  release 内发现 $pending 个媒体文件，先复制进持久化目录: $(basename "$release_dir")"
    unrescued=0
    while IFS= read -r src; do
        rel=${{src#"$src_root"/}}
        dest="$PERSIST_STATIC/$rel"
        mkdir -p "$(dirname "$dest")"
        if [ -f "$dest" ]; then
            if [ "$(sha256sum "$src" | cut -d' ' -f1)" = "$(sha256sum "$dest" | cut -d' ' -f1)" ]; then
                continue
            fi
            dest="$dest.rescued-$(basename "$release_dir")"
            [ -f "$dest" ] && continue
        fi
        if ! cp -p "$src" "$dest"; then
            echo "  [警告] 媒体复制失败，保留 release 不删除: $rel" >&2
            unrescued=$((unrescued + 1))
        fi
    done < <(find "$src_root" -type f 2>/dev/null)

    [ "$unrescued" -eq 0 ] || return 1
    return 0
}}

kept=0

for name in $(find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r); do
    if [ -n "$CURRENT_NAME" ] && [ "$name" = "$CURRENT_NAME" ]; then
        continue
    fi

    if [ "$kept" -lt "$RETAIN_OTHERS" ]; then
        kept=$((kept + 1))
        continue
    fi

    if ! rescue_release_media "$RELEASES_ROOT/$name"; then
        echo "  [跳过删除] 媒体未能全部抢救: $name" >&2
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


def verify_public_reverse_proxy():
    """从服务器本机穿过公网域名对应的 Nginx 配置验证 API 路由。"""
    parsed = urlsplit(REMOTE_SMOKE_BASE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("REMOTE_SMOKE_BASE_URL 必须是有效的 http(s) 地址")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    health_path = f"{parsed.path.rstrip('/')}/api/health"
    health_url = urlunsplit((parsed.scheme, parsed.netloc, health_path, "", ""))
    command = (
        "curl -ksS --max-time 15 "
        f"--resolve {remote_shell_quote(f'{parsed.hostname}:{port}:127.0.0.1')} "
        f"-w '\\n%{{http_code}}' {remote_shell_quote(health_url)}"
    )
    result = run_remote_command(command, check=False)
    if result.returncode != 0:
        raise RuntimeError("公网反向代理健康检查无法连接到 Nginx")

    output_lines = result.stdout.rstrip().splitlines()
    if not output_lines:
        raise RuntimeError("公网反向代理健康检查未返回结果")
    http_code = output_lines[-1].strip()
    response_body = "\n".join(output_lines[:-1]).strip()
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"公网反向代理健康检查返回非 JSON 响应: HTTP {http_code}"
        ) from exc

    print(f"公网反向代理健康检查: HTTP {http_code}")
    if http_code != "200" or payload.get("status") != "ok":
        raise RuntimeError(f"公网反向代理健康检查失败: HTTP {http_code}")


def verify_remote_qrcode_endpoints():
    """请求真实二维码接口，仅输出状态和图片元数据，不泄露令牌或二维码。"""
    current_backend_dir = f"{REMOTE_BASE}/backend/current"
    script = f'''
set -euo pipefail
cd {remote_shell_quote(current_backend_dir)}
timeout 300 {remote_shell_quote(PYTHON_BIN)} - <<'PYEOF'
import base64
import json
import urllib.error
import urllib.request

from legacy.security import _get_effective_admin_token


token = _get_effective_admin_token()
if not token:
    raise SystemExit("管理员令牌未配置，无法验证二维码接口")

for platform, path in (
    ("QQ", "/api/v2/auth/qq/qrcode"),
    ("小红书", "/api/v2/auth/xhs/qrcode"),
):
    request = urllib.request.Request(
        "http://127.0.0.1:{DEPLOY_BACKEND_PORT}" + path,
        headers={{"X-Admin-Token": token}},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = response.status
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        status_path = path.rsplit("/", 1)[0] + "/status"
        status_request = urllib.request.Request(
            "http://127.0.0.1:{DEPLOY_BACKEND_PORT}" + status_path,
            headers={{"X-Admin-Token": token}},
        )
        safe_status = {{}}
        try:
            with urllib.request.urlopen(status_request, timeout=10) as status_response:
                status_payload = json.load(status_response)
                safe_status = {{
                    key: status_payload.get(key)
                    for key in ("status", "detail", "login_status", "login_status_detail")
                    if key in status_payload
                }}
        except Exception:
            pass
        raise SystemExit(
            f"{{platform}} 二维码接口失败: HTTP {{exc.code}} {{detail}} "
            f"状态={{json.dumps(safe_status, ensure_ascii=False)}}"
        ) from exc

    encoded = payload.get("qrcode") or ""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise SystemExit(f"{{platform}} 二维码不是有效 Base64 图片") from exc
    valid_image = (
        raw.startswith(b"\\x89PNG\\r\\n\\x1a\\n")
        or raw.startswith(b"\\xff\\xd8\\xff")
        or (raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
    )
    if not valid_image or len(raw) < 500:
        raise SystemExit(f"{{platform}} 二维码图片内容无效")
    print(f"{{platform}} 二维码接口通过: HTTP {{status}}, image_bytes={{len(raw)}}")
PYEOF
'''
    run_remote_streaming_command(script)


def main():
    args = parse_cli_args()
    validate_deploy_settings()

    if args.unlock_only:
        print(f"连接到 {REMOTE_HOST}:{REMOTE_PORT}...")
        print("仅执行远端部署锁清理\n")
        unlock_remote_deploy_lock()
        return

    print(f"连接到 {REMOTE_HOST}:{REMOTE_PORT}...")
    print("使用系统 OpenSSH 和 npm 执行部署\n")
    release_id = generate_release_id()
    acquire_remote_deploy_lock(
        force_unlock=args.force_unlock,
        lock_max_minutes=max(1, args.lock_max_minutes),
    )

    try:
        ensure_frontend_build()
        frontend_release_dir = prepare_frontend_release(release_id)
        backend_release_dir = prepare_backend_release(release_id)
        upload_backend_release(backend_release_dir)
        upload_frontend_dist(frontend_release_dir)
        install_backend_dependencies(backend_release_dir)
        run_database_migration(backend_release_dir)
        previous_backend_target = get_current_symlink_target(f"{REMOTE_BASE}/backend/current")
        activate_backend_release(release_id)
        try:
            ensure_browser_runtime()
            ensure_baota_security_whitelist()
            verify_browser_runtime()
            configure_baota_python_project()
            restart_backend_service()
            verify_baota_browser_environment()
            restart_arq_worker()
            verify_remote_health()
            verify_public_reverse_proxy()
        except Exception:
            if previous_backend_target:
                restore_backend_release(previous_backend_target)
                restart_backend_service()
                restart_arq_worker()
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
