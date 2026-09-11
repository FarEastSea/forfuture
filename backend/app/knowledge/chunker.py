"""中文分块。用字符窗口而不是 tiktoken，避免再引入依赖。"""
from __future__ import annotations

from app.domain.enums import ChunkType
from app.platforms.base import NormalizedItem  # noqa: F401  类型提示备用


def chunk_text(text: str, *, size: int = 480, overlap: int = 80) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        parts.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def chunks_for_item(
    *,
    title: str | None,
    body: str | None,
    comments: list[str],
    image_captions: list[str],
    size: int,
    overlap: int,
) -> list[tuple[str, str, int]]:
    result: list[tuple[str, str, int]] = []
    if title:
        result.append((ChunkType.TITLE.value, title.strip(), 0))
    for index, part in enumerate(chunk_text(body or "", size=size, overlap=overlap)):
        result.append((ChunkType.BODY.value, part, index))
    if comments:
        digest = "\n".join(comments[:40])
        for index, part in enumerate(chunk_text(digest, size=size, overlap=overlap)):
            result.append((ChunkType.COMMENT_DIGEST.value, part, index))
    for index, caption in enumerate(image_captions):
        if caption.strip():
            result.append((ChunkType.IMAGE_CAPTION.value, caption.strip(), index))
    return result
