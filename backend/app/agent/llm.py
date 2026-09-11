"""LLM 供应商封装。"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.embedder import active_provider


async def chat_stream(
    session: AsyncSession,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    provider = await active_provider(session)
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=provider.api_key, base_url=provider.api_base)
    kwargs: dict[str, Any] = {
        "model": provider.chat_model,
        "messages": messages,
        "stream": True,
        "max_tokens": provider.max_tokens,
        "temperature": temperature if temperature is not None else provider.temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue
        payload: dict[str, Any] = {}
        if delta.content:
            payload["content"] = delta.content
        if delta.tool_calls:
            payload["tool_calls"] = [
                {
                    "index": call.index,
                    "id": call.id,
                    "name": call.function.name if call.function else None,
                    "arguments": call.function.arguments if call.function else "",
                }
                for call in delta.tool_calls
            ]
        if payload:
            yield payload
