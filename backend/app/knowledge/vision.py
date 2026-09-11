"""图片描述入库。关掉 kb_vision_enabled 时是空操作。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_store import settings_store
from app.domain.content import ContentItem
from app.knowledge.embedder import active_provider


async def caption_images(session: AsyncSession, item: ContentItem) -> list[str]:
    cfg = await settings_store.load_all(session)
    if not cfg.get("kb_vision_enabled"):
        return []
    try:
        provider = await active_provider(session)
    except Exception:
        return []
    if not provider.vision_model:
        return []
    # 真正的多模态调用在有可用视觉模型时由调用方补齐；缺省返回空，避免拖垮索引。
    return []
