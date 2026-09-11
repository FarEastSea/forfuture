"""LLM provider configuration with masked secrets."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.api.deps import AdminDep, SessionDep
from app.core.errors import NotFoundError, ValidationError
from app.domain.ops import LLMProvider

router = APIRouter()


class ProviderWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    api_base: str = Field(min_length=1, max_length=500)
    api_key: str = ""
    model: str = Field(min_length=1, max_length=200)
    embed_model: str | None = None
    vision_model: str | None = None
    max_tokens: int = Field(default=4096, ge=1, le=200000)
    temperature: float = Field(default=0.7, ge=0, le=2)


def _masked(value: str) -> str:
    if len(value) > 8:
        return f"{value[:4]}...{value[-4:]}"
    return "****" if value else ""


def _payload(row: LLMProvider) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "api_base": row.api_base,
        "api_key": _masked(row.api_key),
        "model": row.chat_model,
        "embed_model": row.embed_model,
        "vision_model": row.vision_model,
        "max_tokens": row.max_tokens,
        "temperature": row.temperature,
        "is_active": row.is_active,
        "created_at": row.created_at,
    }


@router.get("")
async def list_providers(session: SessionDep, _: AdminDep):
    rows = (await session.execute(select(LLMProvider).order_by(LLMProvider.id.desc()))).scalars().all()
    return {"items": [_payload(row) for row in rows]}


@router.post("")
async def create_provider(payload: ProviderWrite, session: SessionDep, _: AdminDep):
    if not payload.api_key:
        raise ValidationError("新建配置必须提供 API Key")
    row = LLMProvider(
        name=payload.name,
        api_base=payload.api_base,
        api_key=payload.api_key,
        chat_model=payload.model,
        embed_model=payload.embed_model or None,
        vision_model=payload.vision_model or None,
        max_tokens=payload.max_tokens,
        temperature=payload.temperature,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _payload(row)


@router.post("/test-connection")
async def test_provider(payload: ProviderWrite, session: SessionDep, _: AdminDep):
    api_key = payload.api_key
    if not api_key:
        existing = (
            await session.execute(select(LLMProvider).where(LLMProvider.name == payload.name))
        ).scalar_one_or_none()
        api_key = existing.api_key if existing else ""
    if not api_key:
        raise ValidationError("缺少 API Key")
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=payload.api_base)
        result = await client.chat.completions.create(
            model=payload.model,
            messages=[{"role": "user", "content": "Reply OK"}],
            max_tokens=8,
        )
        return {"success": True, "message": "连接成功", "response_chars": len(result.choices[0].message.content or "")}
    except Exception as exc:
        return {"success": False, "message": f"连接失败: {type(exc).__name__}"}


@router.put("/{provider_id}")
async def update_provider(provider_id: int, payload: ProviderWrite, session: SessionDep, _: AdminDep):
    row = await session.get(LLMProvider, provider_id)
    if row is None:
        raise NotFoundError("配置不存在")
    row.name = payload.name
    row.api_base = payload.api_base
    if payload.api_key:
        row.api_key = payload.api_key
    row.chat_model = payload.model
    row.embed_model = payload.embed_model or None
    row.vision_model = payload.vision_model or None
    row.max_tokens = payload.max_tokens
    row.temperature = payload.temperature
    await session.commit()
    return _payload(row)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(LLMProvider, provider_id)
    if row is None:
        raise NotFoundError("配置不存在")
    if row.is_active:
        raise ValidationError("不能删除当前启用的配置")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/{provider_id}/activate")
async def activate_provider(provider_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(LLMProvider, provider_id)
    if row is None:
        raise NotFoundError("配置不存在")
    await session.execute(update(LLMProvider).values(is_active=False))
    row.is_active = True
    await session.commit()
    return {"ok": True}
