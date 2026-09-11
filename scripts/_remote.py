"""远端连接助手：复用 deploy.py 中已验证的 SSH 参数与执行逻辑。

只提供连接与命令执行能力，不包含任何写操作策略。调用方负责保证命令语义安全。
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import deploy  # noqa: E402  依赖其模块级 .env 加载与 SSH 参数解析

REMOTE_BASE = deploy.REMOTE_BASE
REMOTE_HOST = deploy.REMOTE_HOST
REMOTE_PORT = deploy.REMOTE_PORT
REMOTE_USER = deploy.REMOTE_USER
PYTHON_BIN = deploy.PYTHON_BIN


class RemoteUnavailable(RuntimeError):
    """远端不可达或认证失败。"""


_SECRET_PATTERNS = (
    re.compile(r"(PGPASSWORD=)(\S+)"),
    re.compile(r"(DATABASE_PASSWORD=)(\S+)"),
    re.compile(r"(://[^:/@\s]+:)([^@\s]+)(@)"),
)


def redact(text: str) -> str:
    """抹掉命令与输出中的凭据，避免写进日志或终端。"""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "***" + (m.group(3) if m.lastindex == 3 else ""), text)
    return text


def quote(value: object) -> str:
    return shlex.quote(str(value).replace("\\", "/"))


def check_connection_settings() -> list[str]:
    """返回连接配置层面的问题列表（空列表表示配置看起来完整）。"""
    problems: list[str] = []
    if not REMOTE_HOST:
        problems.append("DEPLOY_REMOTE_HOST 未配置")
    if not REMOTE_USER:
        problems.append("DEPLOY_REMOTE_USER 未配置")
    if not REMOTE_BASE:
        problems.append("DEPLOY_REMOTE_BASE 未配置")
    key_path = deploy.REMOTE_KEY_PATH
    if key_path and not Path(key_path).exists():
        problems.append(f"DEPLOY_SSH_KEY_PATH 指向的私钥不存在: {key_path}")
    if not deploy.SSH_CMD:
        problems.append("本机未找到 ssh，可安装 OpenSSH Client")
    return problems


def run(command: str, *, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    """在远端执行一条 bash 命令并捕获输出。"""
    args = deploy.ssh_base_args() + [f"bash -lc {shlex.quote(command)}"]
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode == 255:
        raise RemoteUnavailable(
            "SSH 连接失败（退出码 255）。\n"
            f"stderr: {redact(result.stderr.strip())[:500]}"
        )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"远端命令失败({result.returncode}): {redact(command)[:200]}\n"
            f"stdout: {redact(result.stdout.strip())[:500]}\n"
            f"stderr: {redact(result.stderr.strip())[:500]}"
        )
    return result


def probe() -> None:
    """验证远端可达；不可达时抛出 RemoteUnavailable。"""
    problems = check_connection_settings()
    if problems:
        raise RemoteUnavailable("远端连接配置有问题：\n  - " + "\n  - ".join(problems))
    run("echo remote_ok", timeout=60)


def read_remote_env(keys: list[str]) -> dict[str, str]:
    """读取远端项目根目录 .env 中指定键的值。

    在远端解析并回传，避免把凭据放进命令行参数。
    """
    env_file = f"{REMOTE_BASE}/.env"
    key_list = " ".join(quote(key) for key in keys)
    command = f"""
set -euo pipefail
ENV_FILE={quote(env_file)}
if [ ! -f "$ENV_FILE" ]; then
    echo "__ENV_MISSING__"
    exit 0
fi
for key in {key_list}; do
    line=$(grep -E "^[[:space:]]*${{key}}[[:space:]]*=" "$ENV_FILE" | tail -n1 || true)
    value=${{line#*=}}
    value=$(printf '%s' "$value" | tr -d '\\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
    value=$(printf '%s' "$value" | sed -E "s/^[\\"']//; s/[\\"']$//")
    printf '%s=%s\\n' "$key" "$value"
done
"""
    result = run(command)
    if "__ENV_MISSING__" in result.stdout:
        raise RuntimeError(f"远端 .env 不存在: {env_file}")
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key.strip()] = value
    return parsed
