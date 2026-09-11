"""P0 远端盘点与备份。

对远端做只读盘点，并可选地执行数据库全量备份。产出的 JSON 报告是后续数据迁移的校验基线。

用法：
    python scripts/remote_inventory.py                 # 仅盘点（完全只读）
    python scripts/remote_inventory.py --backup        # 盘点 + pg_dump 全量备份

安全约束：
    - 绝不删除、移动、重命名 backend/static 下的任何文件；本脚本对 static 只做统计。
    - 备份文件写入 {REMOTE_BASE}/backups/，该目录不在 deploy.py 的 release 清理范围内。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from _remote import (  # noqa: E402
    PROJECT_ROOT,
    PYTHON_BIN,
    REMOTE_BASE,
    RemoteUnavailable,
    probe,
    quote,
    read_remote_env,
    run,
)

MEDIA_DIRS = ("qq_images", "xhs_images", "avatars", "qq_videos", "xhs_videos")

DB_ENV_KEYS = [
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_NAME",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
]

# 所有可能存放 /static/ 本地路径的列，用于校验数据库引用与磁盘文件是否一致。
LOCAL_PATH_SOURCES = """
SELECT DISTINCT p FROM (
    SELECT jsonb_array_elements(images::jsonb) ->> 'local_path' AS p
        FROM qq_posts WHERE images IS NOT NULL AND jsonb_typeof(images::jsonb) = 'array'
    UNION ALL SELECT local_video_path FROM qq_posts
    UNION ALL SELECT author_avatar FROM qq_posts
    UNION ALL SELECT author_avatar FROM qq_comments
    UNION ALL SELECT jsonb_array_elements(images::jsonb) ->> 'local_path'
        FROM xhs_notes WHERE images IS NOT NULL AND jsonb_typeof(images::jsonb) = 'array'
    UNION ALL SELECT local_video_path FROM xhs_notes
    UNION ALL SELECT author_avatar FROM xhs_notes
    UNION ALL SELECT author_avatar FROM xhs_comments
    UNION ALL SELECT avatar_url FROM accounts
) t
WHERE p IS NOT NULL AND p LIKE '/static/%'
"""

# 借助 query_to_xml 动态统计每张表行数，无需预先知道表清单。
TABLE_COUNTS_SQL = """
SELECT coalesce(json_agg(json_build_object('table', table_name, 'rows', row_count)
                         ORDER BY table_name), '[]'::json)
FROM (
    SELECT c.relname AS table_name,
           (xpath('/row/cnt/text()',
                  query_to_xml(format('SELECT count(*) AS cnt FROM public.%I', c.relname),
                               false, true, '')))[1]::text::bigint AS row_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
) s
"""

CONTENT_STATS_SQL = """
SELECT json_build_object(
    'qq_posts_with_images', (SELECT count(*) FROM qq_posts
        WHERE images IS NOT NULL AND jsonb_typeof(images::jsonb) = 'array'
          AND jsonb_array_length(images::jsonb) > 0),
    'qq_posts_with_video', (SELECT count(*) FROM qq_posts WHERE local_video_path IS NOT NULL),
    'qq_posts_with_forward', (SELECT count(*) FROM qq_posts
        WHERE forward_content IS NOT NULL AND forward_content <> ''),
    'qq_posts_time_range', (SELECT json_build_array(min(post_time), max(post_time)) FROM qq_posts),
    'xhs_notes_with_images', (SELECT count(*) FROM xhs_notes
        WHERE images IS NOT NULL AND jsonb_typeof(images::jsonb) = 'array'
          AND jsonb_array_length(images::jsonb) > 0),
    'xhs_notes_with_video', (SELECT count(*) FROM xhs_notes WHERE local_video_path IS NOT NULL),
    'xhs_notes_time_range', (SELECT json_build_array(min(post_time), max(post_time)) FROM xhs_notes),
    'accounts_by_kind', (SELECT coalesce(json_agg(row_to_json(a)), '[]'::json) FROM (
        SELECT platform, is_target, status, count(*) AS n
        FROM accounts GROUP BY platform, is_target, status ORDER BY platform, is_target, status) a),
    'referenced_local_paths', (SELECT count(*) FROM (""" + LOCAL_PATH_SOURCES + """) q)
)
"""


_PG_BIN_CACHE: dict[str, str] = {}

# Baota 面板把 PostgreSQL 客户端装在这里，不在 PATH 上。
PG_BIN_CANDIDATE_DIRS = ("/www/server/pgsql/bin", "/usr/lib/postgresql/*/bin", "/usr/bin", "/usr/local/bin")


def resolve_pg_bin(name: str) -> str:
    """定位远端的 psql / pg_dump 等客户端工具，兼容不在 PATH 上的面板安装。"""
    if name in _PG_BIN_CACHE:
        return _PG_BIN_CACHE[name]
    candidates = " ".join(f"{d}/{name}" for d in PG_BIN_CANDIDATE_DIRS)
    command = f"""
found=$(command -v {quote(name)} 2>/dev/null || true)
if [ -n "$found" ]; then echo "$found"; exit 0; fi
for candidate in {candidates}; do
    if [ -x "$candidate" ]; then echo "$candidate"; exit 0; fi
done
echo "__NOT_FOUND__"
"""
    path = run(command, check=False).stdout.strip().splitlines()[-1].strip()
    if not path or path == "__NOT_FOUND__":
        raise RuntimeError(f"远端未找到 {name}，无法继续。请确认 PostgreSQL 客户端已安装。")
    _PG_BIN_CACHE[name] = path
    return path


def psql_json(db: dict[str, str], sql: str) -> object:
    """在远端执行一条返回单个 JSON 值的查询。"""
    psql = resolve_pg_bin("psql")
    command = f"""
set -euo pipefail
export PGPASSWORD={quote(db['password'])}
{quote(psql)} -h {quote(db['host'])} -p {quote(db['port'])} -U {quote(db['user'])} -d {quote(db['name'])} \
     -v ON_ERROR_STOP=1 -At -c {quote(sql)}
"""
    result = run(command)
    raw = result.stdout.strip()
    return json.loads(raw) if raw else None


def collect_db_settings() -> dict[str, str]:
    env = read_remote_env(DB_ENV_KEYS)
    return {
        "host": env.get("DATABASE_HOST") or "127.0.0.1",
        "port": env.get("DATABASE_PORT") or "5432",
        "name": env.get("DATABASE_NAME") or "AI_records_and_reminders",
        "user": env.get("DATABASE_USER") or "AI_records_and_reminders",
        "password": env.get("DATABASE_PASSWORD") or "",
    }


def collect_environment() -> dict[str, object]:
    command = f"""
set -uo pipefail
echo "__OS__"; (cat /etc/os-release 2>/dev/null | grep -E '^(PRETTY_NAME|VERSION_ID)=' || echo unknown)
echo "__KERNEL__"; uname -r
echo "__CPU__"; nproc
echo "__MEM_MB__"; free -m | awk '/^Mem:/ {{print $2}}'
echo "__DISK__"; df -h {quote(REMOTE_BASE)} | tail -n1
echo "__CHROME__"
for bin in google-chrome-stable google-chrome chromium chromium-browser; do
    path=$(command -v "$bin" 2>/dev/null || true)
    if [ -n "$path" ]; then echo "$bin=$path ($("$path" --version 2>/dev/null || echo '?'))"; fi
done
echo "__REDIS__"
redis_path=$(command -v redis-server 2>/dev/null || true)
echo "redis-server=${{redis_path:-missing}}"
if command -v redis-cli >/dev/null 2>&1; then echo "redis-ping=$(redis-cli ping 2>&1 | head -n1)"; fi
echo "__PG_TOOLS__"
echo "psql=$(command -v psql 2>/dev/null || echo missing)"
echo "pg_dump=$(command -v pg_dump 2>/dev/null || echo missing)"
psql --version 2>/dev/null || true
echo "__PYTHON__"; {quote(PYTHON_BIN)} --version 2>&1 || echo missing
echo "__NODE__"; (node --version 2>/dev/null || echo missing)
echo "__STATIC__"
STATIC_ROOT={quote(f"{REMOTE_BASE}/backend/static")}
for d in {" ".join(MEDIA_DIRS)}; do
    dir="$STATIC_ROOT/$d"
    if [ -d "$dir" ]; then
        count=$(find "$dir" -type f | wc -l)
        bytes=$(du -sb "$dir" 2>/dev/null | cut -f1)
        echo "$d=$count:$bytes"
    else
        echo "$d=missing:0"
    fi
done
echo "__STATIC_TOTAL__"
if [ -d "$STATIC_ROOT" ]; then
    echo "files=$(find "$STATIC_ROOT" -type f | wc -l):bytes=$(du -sb "$STATIC_ROOT" | cut -f1)"
else
    echo "files=0:bytes=0"
fi
echo "__RELEASES__"
echo "backend_current=$(readlink -f {quote(f'{REMOTE_BASE}/backend/current')} 2>/dev/null || echo missing)"
echo "frontend_dist=$(readlink -f {quote(f'{REMOTE_BASE}/frontend/dist')} 2>/dev/null || echo missing)"
"""
    result = run(command, check=False)
    sections: dict[str, list[str]] = {}
    current = "_"
    for line in result.stdout.splitlines():
        if line.startswith("__") and line.endswith("__"):
            current = line.strip("_").lower()
            sections[current] = []
        elif line.strip():
            sections.setdefault(current, []).append(line.rstrip())

    media: dict[str, dict[str, int | str]] = {}
    for entry in sections.get("static", []):
        name, _, payload = entry.partition("=")
        count, _, size = payload.partition(":")
        media[name] = (
            {"exists": False, "files": 0, "bytes": 0}
            if count == "missing"
            else {"exists": True, "files": int(count or 0), "bytes": int(size or 0)}
        )

    return {
        "os": sections.get("os", []),
        "kernel": "".join(sections.get("kernel", [])),
        "cpu_cores": "".join(sections.get("cpu", [])),
        "memory_mb": "".join(sections.get("mem_mb", [])),
        "disk": "".join(sections.get("disk", [])),
        "chrome": sections.get("chrome", []) or ["missing"],
        "redis": sections.get("redis", []),
        "pg_tools": sections.get("pg_tools", []),
        "python": sections.get("python", []),
        "node": sections.get("node", []),
        "media_dirs": media,
        "static_total": "".join(sections.get("static_total", [])),
        "releases": sections.get("releases", []),
    }


def check_media_references(db: dict[str, str]) -> dict[str, object]:
    """核对数据库引用的 /static/ 路径在磁盘上是否真实存在。只读。"""
    psql = resolve_pg_bin("psql")
    command = f"""
set -euo pipefail
export PGPASSWORD={quote(db['password'])}
STATIC_PARENT={quote(f"{REMOTE_BASE}/backend")}
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
{quote(psql)} -h {quote(db['host'])} -p {quote(db['port'])} -U {quote(db['user'])} -d {quote(db['name'])} \
     -v ON_ERROR_STOP=1 -At -c {quote(LOCAL_PATH_SOURCES)} > "$TMP"
total=0
missing=0
: > /tmp/_missing_sample.txt
while IFS= read -r p; do
    [ -z "$p" ] && continue
    total=$((total + 1))
    if [ ! -f "$STATIC_PARENT$p" ]; then
        missing=$((missing + 1))
        if [ "$missing" -le 20 ]; then printf '%s\\n' "$p" >> /tmp/_missing_sample.txt; fi
    fi
done < "$TMP"
echo "total=$total"
echo "missing=$missing"
echo "__SAMPLE__"
cat /tmp/_missing_sample.txt
rm -f /tmp/_missing_sample.txt
"""
    result = run(command, timeout=900)
    total = missing = 0
    sample: list[str] = []
    in_sample = False
    for line in result.stdout.splitlines():
        if line.strip() == "__SAMPLE__":
            in_sample = True
        elif in_sample:
            if line.strip():
                sample.append(line.strip())
        elif line.startswith("total="):
            total = int(line.split("=", 1)[1] or 0)
        elif line.startswith("missing="):
            missing = int(line.split("=", 1)[1] or 0)
    return {"referenced": total, "missing_on_disk": missing, "missing_sample": sample}


def check_pgvector(db: dict[str, str]) -> dict[str, object]:
    sql = (
        "SELECT json_build_object("
        "'available', (SELECT count(*) FROM pg_available_extensions WHERE name='vector'),"
        "'installed', (SELECT count(*) FROM pg_extension WHERE extname='vector'),"
        "'server_version', current_setting('server_version'))"
    )
    return psql_json(db, sql)  # type: ignore[return-value]


def run_backup(db: dict[str, str]) -> dict[str, object]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = f"{REMOTE_BASE}/backups"
    dump_path = f"{backup_dir}/db-{stamp}.dump"
    pg_dump = resolve_pg_bin("pg_dump")
    pg_restore = resolve_pg_bin("pg_restore")
    command = f"""
set -euo pipefail
export PGPASSWORD={quote(db['password'])}
mkdir -p {quote(backup_dir)}
{quote(pg_dump)} -h {quote(db['host'])} -p {quote(db['port'])} -U {quote(db['user'])} -d {quote(db['name'])} \
        -Fc -f {quote(dump_path)}
ls -l {quote(dump_path)} | awk '{{print "size_bytes=" $5}}'
echo "toc_entries=$({quote(pg_restore)} --list {quote(dump_path)} | grep -c '^[0-9]' || true)"
echo "path={dump_path}"
"""
    result = run(command, timeout=3600)
    info: dict[str, object] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            info[key.strip()] = value.strip()
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description="远端盘点与数据库备份（只读 + 备份）")
    parser.add_argument("--backup", action="store_true", help="额外执行 pg_dump 全量备份")
    parser.add_argument(
        "--skip-media-check",
        action="store_true",
        help="跳过逐条核对数据库引用的媒体文件是否存在（该步骤较慢）",
    )
    args = parser.parse_args()

    print("=== P0 远端盘点 ===")
    try:
        probe()
    except RemoteUnavailable as exc:
        print(f"\n远端不可达：\n{exc}\n")
        print("请确认：SSH 端口是否开放、DEPLOY_SSH_KEY_PATH 是否指向真实存在的私钥、服务器是否在线。")
        return 2

    print("  连接正常，开始采集环境信息")
    environment = collect_environment()

    print("  读取远端数据库配置")
    db = collect_db_settings()

    print("  统计各表行数")
    tables = psql_json(db, TABLE_COUNTS_SQL)

    print("  统计内容分布")
    content = psql_json(db, CONTENT_STATS_SQL)

    print("  检查 pgvector 可用性")
    vector = check_pgvector(db)

    media_check: dict[str, object] | None = None
    if not args.skip_media_check:
        print("  核对数据库引用的媒体文件是否存在于磁盘（只读）")
        media_check = check_media_references(db)

    backup: dict[str, object] | None = None
    if args.backup:
        print("  执行 pg_dump 全量备份")
        backup = run_backup(db)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "remote_base": REMOTE_BASE,
        "database": {"host": db["host"], "port": db["port"], "name": db["name"], "user": db["user"]},
        "environment": environment,
        "tables": tables,
        "content": content,
        "pgvector": vector,
        "media_reference_check": media_check,
        "backup": backup,
    }

    out_dir = PROJECT_ROOT / ".tmp"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"remote-inventory-{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 盘点结果 ===")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:6000])
    print(f"\n完整报告已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
