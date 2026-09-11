"""抢救散落在 deploy release 目录里的已下载媒体资源。

背景：远端 .env 里 STATIC_DIR 是相对路径 ./static，后端进程以 release 目录为工作目录运行时，
下载的媒体会落到 {release}/static/ 而不是持久化的 backend/static/。deploy.py 轮换 release 时
会 rm -rf 旧 release，这些不可再生的抓取资源就会被永久删除。

本脚本把 release 里的媒体**复制**进持久化目录，让它们脱离清理范围。

安全约束（对应仓库最高优先级规则）：
    - 只做复制，绝不删除、移动、重命名任何源文件。
    - 目标已存在且内容相同 -> 跳过。
    - 目标已存在但内容不同 -> 另存为 <名字>.rescued-<release><后缀>，不覆盖任何东西。
    - 复制后逐个用 sha256 校验。

用法：
    python scripts/rescue_release_media.py            # 演练，只报告不落盘
    python scripts/rescue_release_media.py --apply    # 实际复制
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from _remote import PROJECT_ROOT, REMOTE_BASE, RemoteUnavailable, probe, quote, run

MEDIA_DIR_NAMES = ("qq_images", "xhs_images", "avatars", "qq_videos", "xhs_videos")


def build_script(*, apply: bool) -> str:
    media_globs = " ".join(f"-o -name {quote(name)}" for name in MEDIA_DIR_NAMES)
    media_globs = media_globs[3:]  # 去掉开头多余的 -o
    return f"""
set -uo pipefail
BASE={quote(REMOTE_BASE)}
RELEASES_ROOT="$BASE/backend/.deploy/releases"
PERSIST="$BASE/backend/static"
APPLY={"1" if apply else "0"}

if [ ! -d "$RELEASES_ROOT" ]; then
    echo "RESULT|no-releases-dir"
    exit 0
fi

mkdir -p "$PERSIST"

copied=0
skipped_same=0
conflicts=0
failed=0
scanned=0

# 遍历所有 release 下 static 里的媒体目录
find "$RELEASES_ROOT" -type d \\( {media_globs} \\) -path '*/static/*' 2>/dev/null | sort | while read -r src_dir; do
    subdir=$(basename "$src_dir")
    release=$(printf '%s' "$src_dir" | sed -E "s|^$RELEASES_ROOT/([^/]+)/.*|\\1|")
    dest_dir="$PERSIST/$subdir"
    [ "$APPLY" = "1" ] && mkdir -p "$dest_dir"

    find "$src_dir" -type f 2>/dev/null | while read -r src; do
        fname=$(basename "$src")
        dest="$dest_dir/$fname"
        if [ -f "$dest" ]; then
            src_hash=$(sha256sum "$src" | cut -d' ' -f1)
            dest_hash=$(sha256sum "$dest" | cut -d' ' -f1)
            if [ "$src_hash" = "$dest_hash" ]; then
                echo "SAME|$subdir/$fname"
            else
                stem="${{fname%.*}}"
                ext="${{fname##*.}}"
                if [ "$stem" = "$fname" ]; then
                    alt="$dest_dir/$fname.rescued-$release"
                else
                    alt="$dest_dir/$stem.rescued-$release.$ext"
                fi
                if [ "$APPLY" = "1" ]; then
                    if cp -p "$src" "$alt"; then echo "CONFLICT_COPIED|$subdir/$fname|$alt"; else echo "FAIL|$subdir/$fname"; fi
                else
                    echo "CONFLICT_WOULD_COPY|$subdir/$fname|$alt"
                fi
            fi
        else
            if [ "$APPLY" = "1" ]; then
                if cp -p "$src" "$dest"; then
                    src_hash=$(sha256sum "$src" | cut -d' ' -f1)
                    dest_hash=$(sha256sum "$dest" | cut -d' ' -f1)
                    if [ "$src_hash" = "$dest_hash" ]; then
                        echo "COPIED|$subdir/$fname"
                    else
                        echo "FAIL_VERIFY|$subdir/$fname"
                    fi
                else
                    echo "FAIL|$subdir/$fname"
                fi
            else
                echo "WOULD_COPY|$subdir/$fname"
            fi
        fi
    done
done

echo "__SUMMARY__"
echo "release_media_files=$(find "$RELEASES_ROOT" -type f -path '*/static/*' 2>/dev/null | wc -l)"
echo "persist_files_now=$(find "$PERSIST" -type f 2>/dev/null | wc -l)"
echo "persist_bytes_now=$(du -sb "$PERSIST" 2>/dev/null | cut -f1)"
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="把 release 目录里的媒体复制进持久化 static（只复制不删除）")
    parser.add_argument("--apply", action="store_true", help="实际执行复制；缺省为演练模式")
    args = parser.parse_args()

    print("=== 抢救 release 目录中的媒体资源 ===")
    print(f"模式: {'实际复制' if args.apply else '演练（不写入）'}")
    try:
        probe()
    except RemoteUnavailable as exc:
        print(f"远端不可达：{exc}")
        return 2

    result = run(build_script(apply=args.apply), check=False, timeout=3600)

    tally: dict[str, int] = {}
    conflicts: list[str] = []
    summary: dict[str, str] = {}
    in_summary = False
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "__SUMMARY__":
            in_summary = True
            continue
        if in_summary:
            key, _, value = line.partition("=")
            summary[key] = value
            continue
        kind = line.split("|", 1)[0]
        tally[kind] = tally.get(kind, 0) + 1
        if kind.startswith("CONFLICT") or kind.startswith("FAIL"):
            conflicts.append(line)

    print("\n--- 结果 ---")
    for kind, count in sorted(tally.items()):
        print(f"  {kind}: {count}")
    if conflicts:
        print("\n--- 需要人工确认的条目（最多 30 条）---")
        for entry in conflicts[:30]:
            print(f"  {entry}")
    print("\n--- 汇总 ---")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    if result.stderr.strip():
        print(f"\nstderr: {result.stderr.strip()[:800]}")

    report_dir = PROJECT_ROOT / ".tmp"
    report_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = "apply" if args.apply else "dryrun"
    report_path = report_dir / f"rescue-media-{mode}-{stamp}.json"
    report_path.write_text(
        json.dumps(
            {"mode": mode, "tally": tally, "conflicts": conflicts, "summary": summary},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n报告已写入: {report_path}")

    if not args.apply:
        print("\n这是演练结果。确认无误后加 --apply 实际执行。源文件在任何模式下都不会被删除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
