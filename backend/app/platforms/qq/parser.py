"""QQ 空间解析器：g_tk、JSONP、说说/评论字段映射。

这些算法从旧 crawler 原样搬过来，用真实响应 fixture 做回归，避免重写时丢字段。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from app.core.errors import CredentialExpiredError, CrawlError
from app.domain.enums import ContentType, MediaKind, Platform
from app.platforms.base import CrawlPage, NormalizedComment, NormalizedItem, NormalizedMedia
from app.platforms.qq import constants as C

JSONP_RE = re.compile(rf"{re.escape(C.JSONP_CALLBACK)}\((.*)\)", re.DOTALL)


def _looks_like_login_page(text: str) -> bool:
    """识别真正的登录页/跳转，避免误伤正常 JSONP 的 login 状态字段。"""
    prefix = (text or "").lstrip()[:1200].lower()
    return (
        prefix.startswith("<!doctype html")
        or prefix.startswith("<html")
        or "ptlogin2.qq.com" in prefix
        or ("window.location" in prefix and "login" in prefix)
    )


def compute_g_tk(p_skey: str) -> int:
    """DJB 哈希，QQ 空间 API 鉴权 token。算法二十年未变。"""
    h = 5381
    for char in p_skey:
        h += (h << 5) + ord(char)
    return h & 0x7FFFFFFF


def extract_gtk_source(cookies: list[dict[str, Any]] | dict[str, str]) -> str:
    """p_skey → skey → pt_key，与旧实现优先级一致。"""
    mapping = _cookie_map(cookies)
    for key in ("p_skey", "skey", "pt_key"):
        value = mapping.get(key) or mapping.get(key.upper())
        if value:
            return value
    return ""


def _cookie_map(cookies: list[dict[str, Any]] | dict[str, str]) -> dict[str, str]:
    if isinstance(cookies, dict):
        return {str(k): str(v) for k, v in cookies.items() if v}
    result: dict[str, str] = {}
    for item in cookies:
        name = item.get("name")
        value = item.get("value")
        if name and value is not None:
            result[str(name)] = str(value)
    return result


def unwrap_jsonp(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise CrawlError("QQ 空间返回空响应")
    if _looks_like_login_page(stripped):
        raise CredentialExpiredError("QQ 空间 Cookie 已过期（返回登录页）")

    match = JSONP_RE.search(stripped)
    payload = match.group(1) if match else stripped
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CrawlError("QQ 空间返回无法解析的 JSONP") from exc
    if not isinstance(data, dict):
        raise CrawlError("QQ 空间返回非对象 JSON")
    return data


def interpret_code(data: dict[str, Any]) -> None:
    code = data.get("code")
    if code in (None, 0, "0"):
        return
    try:
        numeric = int(code)
    except (TypeError, ValueError):
        numeric = None
    message = str(data.get("message") or data.get("msg") or f"code={code}")
    if numeric == C.COOKIE_EXPIRED_CODE:
        raise CredentialExpiredError(f"QQ 空间 Cookie 已过期: {message}")
    raise CrawlError(f"QQ 空间接口错误: {message}", detail={"code": code})


def _unix_to_dt(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1e12:
        number /= 1000
    try:
        return datetime.fromtimestamp(number)
    except (OverflowError, OSError, ValueError):
        return None


def _candidates(*values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        url = str(value or "").strip()
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _fingerprint_tid(msg: dict[str, Any], qq: str) -> str:
    tid = str(msg.get("tid") or "").strip()
    if tid:
        return tid
    raw = "|".join(
        [
            str(msg.get("content") or ""),
            str(msg.get("created_time") or ""),
            str(len(msg.get("pic") or [])),
            str(bool(msg.get("video"))),
        ]
    )
    return hashlib.sha1(f"{qq}:{raw}".encode("utf-8")).hexdigest()[:16]


def parse_message(msg: dict[str, Any], *, target_uid: str) -> NormalizedItem:
    pics = msg.get("pic") or []
    media: list[NormalizedMedia] = []
    for pic in pics if isinstance(pics, list) else []:
        if not isinstance(pic, dict):
            continue
        urls = _candidates(pic.get("url3"), pic.get("url2"), pic.get("url1"))
        if urls:
            media.append(NormalizedMedia(kind=MediaKind.IMAGE, url=urls[0], fallback_urls=urls[1:]))

    videos = msg.get("video") or []
    if isinstance(videos, dict):
        videos = [videos]
    for video in videos if isinstance(videos, list) else []:
        if not isinstance(video, dict):
            continue
        urls = _candidates(
            video.get("url3"), video.get("url2"), video.get("url1"), video.get("video_url")
        )
        if urls:
            media.append(
                NormalizedMedia(kind=MediaKind.VIDEO, url=urls[0], fallback_urls=urls[1:], role="video")
            )

    author_qq = str(msg.get("uin") or target_uid)
    lbs = msg.get("lbs") if isinstance(msg.get("lbs"), dict) else {}
    rt_con = msg.get("rt_con") if isinstance(msg.get("rt_con"), dict) else {}
    extra: dict[str, Any] = {}
    forward = rt_con.get("content")
    if forward:
        extra["forward_content"] = forward

    return NormalizedItem(
        platform=Platform.QQ,
        platform_item_id=_fingerprint_tid(msg, target_uid),
        content_type=ContentType.QQ_MOMENT,
        author_uid=author_qq,
        author_name=msg.get("name"),
        author_avatar_url=f"{C.AVATAR_URL}?dst_uin={author_qq}&spec=640&img_type=jpg",
        body=msg.get("content") or "",
        posted_at=_unix_to_dt(msg.get("created_time")),
        geo_location=lbs.get("idname") or lbs.get("name"),
        device=msg.get("source_name"),
        metrics={
            "like": int(msg.get("likenum") or 0),
            "comment": int(msg.get("commentnum") or 0),
        },
        extra=extra,
        media=media,
        comments=parse_comment_list(msg.get("commentlist")),
        raw=msg,
    )


def parse_comment_list(raw: Any) -> list[NormalizedComment]:
    comments: list[NormalizedComment] = []
    if not isinstance(raw, list):
        return comments
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("tid") or "").strip()
        if not cid:
            fingerprint = "|".join(
                [str(item.get("uin") or ""), str(item.get("content") or ""), str(item.get("create_time") or "")]
            )
            cid = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
        comments.append(
            NormalizedComment(
                platform_comment_id=cid,
                author_uid=str(item.get("uin") or "") or None,
                author_name=item.get("name"),
                body=item.get("content"),
                commented_at=_unix_to_dt(item.get("create_time")),
                raw=item,
            )
        )
    return comments


class QQParser:
    def parse_feed(self, payload: Any, *, target_uid: str) -> CrawlPage:
        if isinstance(payload, (bytes, str)):
            data = unwrap_jsonp(payload if isinstance(payload, str) else payload.decode("utf-8", "ignore"))
        else:
            data = payload
        interpret_code(data)
        msglist = data.get("msglist") or []
        if not isinstance(msglist, list):
            msglist = []
        items = [parse_message(msg, target_uid=target_uid) for msg in msglist if isinstance(msg, dict)]
        exhausted = len(msglist) < C.PAGE_SIZE
        return CrawlPage(items=items, exhausted=exhausted, raw=data)

    def parse_item_detail(self, payload: Any) -> NormalizedItem | None:
        page = self.parse_feed(payload, target_uid="")
        return page.items[0] if page.items else None

    def parse_comments(self, payload: Any) -> tuple[list[NormalizedComment], str | None, bool]:
        if isinstance(payload, (bytes, str)):
            data = unwrap_jsonp(payload if isinstance(payload, str) else payload.decode("utf-8", "ignore"))
        else:
            data = payload
        interpret_code(data)
        comments = (
            data.get("commentlist")
            or data.get("comments")
            or (data.get("msg") or {}).get("commentlist")
            or []
        )
        parsed = parse_comment_list(comments)
        exhausted = len(parsed) < C.COMMENT_PAGE_SIZE
        return parsed, None, exhausted
