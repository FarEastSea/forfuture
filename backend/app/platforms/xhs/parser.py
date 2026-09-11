"""小红书解析器：图片优选、视频流选择、计数、IP 属地清洗。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from app.domain.enums import ContentType, MediaKind, Platform
from app.platforms.base import CrawlPage, NormalizedComment, NormalizedItem, NormalizedMedia

_INVALID_IP_KEYWORDS = (
    "回到顶部", "返回顶部", "顶部", "展开", "收起", "评论", "点赞",
    "分享", "收藏", "关注", "登录", "扫码", "发布", "笔记",
)


def parse_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    try:
        if "万" in text:
            return int(float(text.replace("万", "")) * 10000)
        if "亿" in text:
            return int(float(text.replace("亿", "")) * 100000000)
        return int(float(text))
    except (ValueError, TypeError):
        return 0


def clean_ip_location(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^IP属地[：:]\s*", "", text).strip()
    if any(keyword in text for keyword in _INVALID_IP_KEYWORDS):
        return ""
    if len(text) > 12 or any(ch.isspace() for ch in text):
        return ""
    if re.search(r"[<>{}\[\]()/\\]", text):
        return ""
    return text


def _ms_to_dt(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1e11:
        number /= 1000
    try:
        return datetime.fromtimestamp(number)
    except (OverflowError, OSError, ValueError):
        return None


def _is_processed_or_webp(url: str) -> bool:
    lowered = url.lower()
    path = urlparse(url).path.lower()
    if ".webp" in path or path.endswith(".webp"):
        return True
    if "!nd_dft" in lowered:
        return True
    if "_webp_" in lowered or "!webp" in lowered:
        return True
    return False


def pick_image_url(img: dict[str, Any]) -> str | None:
    """优先非 WebP、非 !nd_dft 的原始 URL，再从 infoList 倒序挑非 WebP。"""
    original = (
        img.get("url_default")
        or img.get("urlDefault")
        or img.get("url_pre")
        or img.get("urlPre")
        or ""
    )
    if original and not _is_processed_or_webp(original):
        return original

    info_list = img.get("info_list") or img.get("infoList") or []
    if isinstance(info_list, list):
        for info in reversed(info_list):
            if not isinstance(info, dict):
                continue
            url = info.get("url") or info.get("image_url") or ""
            if url and not _is_processed_or_webp(url):
                return url
        for info in reversed(info_list):
            if isinstance(info, dict):
                url = info.get("url") or info.get("image_url") or ""
                if url:
                    return url
    if original:
        return original
    return img.get("url") or None


def pick_cover_url(cover: dict[str, Any]) -> str | None:
    info_list = cover.get("info_list") or cover.get("infoList") or []
    if isinstance(info_list, list):
        for item in reversed(info_list):
            if isinstance(item, dict):
                url = item.get("url") or item.get("image_url")
                if url:
                    return url
    return cover.get("url") or cover.get("urlDefault")


def pick_video_url(video: dict[str, Any]) -> tuple[str | None, int | None]:
    media = video.get("media") or {}
    stream = (media.get("stream") or {}) if isinstance(media, dict) else {}
    for quality in ("h265", "h266", "av1", "h264"):
        streams = stream.get(quality) or []
        if not streams:
            continue
        best = max(streams, key=lambda s: int(s.get("id") or 0))
        url = best.get("master_url") or ""
        if not url:
            backups = best.get("backup_urls") or []
            url = backups[0] if backups else ""
        if url:
            duration = None
            capa = video.get("capa") or {}
            if isinstance(capa, dict) and capa.get("duration"):
                try:
                    duration = int(float(capa["duration"]))
                    if duration > 10000:
                        duration = duration // 1000
                except (TypeError, ValueError):
                    duration = None
            return url, duration
    url = video.get("url") or ""
    if not url:
        consumer = video.get("consumer") or {}
        candidate = consumer.get("origin_video_key") if isinstance(consumer, dict) else ""
        url = candidate if str(candidate).startswith("http") else ""
    return (url or None), None


def extract_note_card(payload: dict[str, Any]) -> dict[str, Any]:
    if "note_card" in payload and isinstance(payload["note_card"], dict):
        return payload["note_card"]
    if "noteCard" in payload and isinstance(payload["noteCard"], dict):
        return payload["noteCard"]
    items = (payload.get("data") or {}).get("items") if isinstance(payload.get("data"), dict) else None
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict) and isinstance(first.get("note_card"), dict):
            return first["note_card"]
    return payload


def parse_note(note: dict[str, Any], *, target_uid: str) -> NormalizedItem | None:
    card = extract_note_card(note)
    note_id = str(
        note.get("note_id")
        or note.get("id")
        or card.get("note_id")
        or card.get("noteId")
        or card.get("id")
        or ""
    ).strip()
    if not note_id:
        return None

    image_list = (
        card.get("images_list")
        or card.get("image_list")
        or card.get("imageList")
        or note.get("images_list")
        or note.get("image_list")
        or []
    )
    images: list[NormalizedMedia] = []
    for img in image_list if isinstance(image_list, list) else []:
        if not isinstance(img, dict):
            continue
        url = pick_image_url(img)
        if url:
            images.append(NormalizedMedia(kind=MediaKind.IMAGE, url=url))
    if not images:
        cover = card.get("cover") or note.get("cover") or {}
        if isinstance(cover, dict):
            cover_url = pick_cover_url(cover)
            if cover_url:
                images.append(NormalizedMedia(kind=MediaKind.IMAGE, url=cover_url, role="cover"))

    video = card.get("video") if isinstance(card.get("video"), dict) else None
    if video:
        video_url, duration = pick_video_url(video)
        if video_url:
            images.append(
                NormalizedMedia(
                    kind=MediaKind.VIDEO, url=video_url, duration_seconds=duration, role="video"
                )
            )

    user = card.get("user") or note.get("user") or {}
    interact = card.get("interact_info") or card.get("interactInfo") or {}
    tags = [
        t.get("name")
        for t in (card.get("tag_list") or [])
        if isinstance(t, dict) and t.get("name")
    ]
    at_users = card.get("at_user_list") or []
    extra: dict[str, Any] = {"note_type": card.get("type") or note.get("type")}
    if tags:
        extra["tags"] = tags
    if at_users:
        extra["at_users"] = at_users

    return NormalizedItem(
        platform=Platform.XHS,
        platform_item_id=note_id,
        content_type=ContentType.XHS_NOTE,
        author_uid=str(user.get("user_id") or user.get("userid") or target_uid or "") or None,
        author_name=user.get("nickname") or user.get("nick_name"),
        author_avatar_url=user.get("avatar") or user.get("image") or user.get("imageb"),
        title=card.get("title") or card.get("display_title") or note.get("display_title"),
        body=card.get("desc") or card.get("content") or note.get("desc"),
        posted_at=_ms_to_dt(card.get("time") or note.get("time")),
        edited_at=_ms_to_dt(card.get("last_update_time")),
        ip_location=clean_ip_location(card.get("ip_location") or card.get("ipLocation")),
        geo_location=card.get("location") if isinstance(card.get("location"), str) else None,
        source_url=f"https://www.xiaohongshu.com/explore/{note_id}",
        metrics={
            "like": parse_count(interact.get("liked_count") or interact.get("like_count")),
            "collect": parse_count(interact.get("collected_count")),
            "comment": parse_count(interact.get("comment_count")),
            "share": parse_count(interact.get("share_count")),
        },
        extra=extra,
        media=images,
        raw=note,
    )


def parse_comment(item: dict[str, Any]) -> NormalizedComment:
    user = item.get("user_info") or item.get("user") or {}
    tags = item.get("show_tags") or []
    parent = item.get("_parent_comment_id") or item.get("target_comment", {}).get("id")
    target = (item.get("target_comment") or {}).get("user_info") or {}
    return NormalizedComment(
        platform_comment_id=str(item.get("id") or ""),
        parent_platform_id=str(parent) if parent else None,
        author_uid=str(user.get("user_id") or "") or None,
        author_name=user.get("nickname"),
        author_avatar_url=user.get("image") or user.get("avatar"),
        body=item.get("content"),
        like_count=parse_count(item.get("like_count")),
        sub_comment_count=parse_count(item.get("sub_comment_count")),
        commented_at=_ms_to_dt(item.get("create_time")),
        ip_location=clean_ip_location(item.get("ip_location")),
        reply_to_name=target.get("nickname"),
        is_author_reply="is_author" in tags if isinstance(tags, list) else bool(item.get("is_author")),
        raw=item,
    )


class XHSParser:
    def parse_feed(self, payload: Any, *, target_uid: str) -> CrawlPage:
        data = payload.get("data") if isinstance(payload, dict) else None
        notes = []
        has_more = False
        cursor = None
        if isinstance(data, dict):
            notes = data.get("notes") or data.get("items") or []
            has_more = bool(data.get("has_more"))
            cursor = data.get("cursor") or data.get("next_cursor")
        elif isinstance(payload, list):
            notes = payload
        items: list[NormalizedItem] = []
        for note in notes:
            if not isinstance(note, dict):
                continue
            parsed = parse_note(note, target_uid=target_uid)
            if parsed:
                items.append(parsed)
        exhausted = not has_more
        return CrawlPage(items=items, next_cursor=cursor, exhausted=exhausted, raw=payload)

    def parse_item_detail(self, payload: Any) -> NormalizedItem | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        items = data.get("items") if isinstance(data, dict) else None
        source = items[0] if isinstance(items, list) and items else data
        if not isinstance(source, dict):
            return None
        return parse_note(source, target_uid="")

    def parse_comments(self, payload: Any) -> tuple[list[NormalizedComment], str | None, bool]:
        data = payload.get("data") if isinstance(payload, dict) else {}
        raw_comments = (data or {}).get("comments") or []
        parsed: list[NormalizedComment] = []
        for item in raw_comments:
            if not isinstance(item, dict):
                continue
            parsed.append(parse_comment(item))
            for sub in item.get("sub_comments") or []:
                if isinstance(sub, dict):
                    sub = {**sub, "_parent_comment_id": item.get("id")}
                    parsed.append(parse_comment(sub))
        has_more = bool((data or {}).get("has_more"))
        cursor = (data or {}).get("cursor") or (data or {}).get("next_cursor")
        return parsed, cursor, not has_more
