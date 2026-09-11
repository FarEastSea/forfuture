"""工具调用循环。过程写入 agent_tool_calls，前端可回放。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.llm import chat_stream
from app.agent.tools import OPENAI_TOOLS, dispatch
from app.core.settings_store import settings_store
from app.domain.enums import MessageRole
from app.domain.ops import AgentMessage, AgentSession, AgentToolCall


async def run_turn(
    session: AsyncSession, session_id: int, user_text: str
) -> AsyncIterator[dict[str, Any]]:
    cfg = await settings_store.load_all(session)
    chat = await session.get(AgentSession, session_id)
    if chat is None:
        raise ValueError("session not found")

    user_msg = AgentMessage(
        session_id=session_id,
        role=MessageRole.USER.value,
        content=user_text,
        created_at=datetime.now(),
    )
    session.add(user_msg)
    await session.flush()

    history = (
        (
            await session.execute(
                select(AgentMessage)
                .where(AgentMessage.session_id == session_id)
                .order_by(AgentMessage.created_at, AgentMessage.id)
            )
        )
        .scalars()
        .all()
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "你是 QQ 空间/小红书动态知识库助手。需要查记录、触发采集或发通知时调用工具。"
                "不要编造未检索到的内容。"
            ),
        }
    ]
    for item in history:
        if item.role in {MessageRole.USER.value, MessageRole.ASSISTANT.value, MessageRole.SYSTEM.value}:
            messages.append({"role": item.role, "content": item.content or ""})

    assistant = AgentMessage(
        session_id=session_id,
        role=MessageRole.ASSISTANT.value,
        content="",
        created_at=datetime.now(),
    )
    session.add(assistant)
    await session.flush()

    tools_enabled = bool(cfg.get("agent_tools_enabled", True))
    max_iter = int(cfg.get("agent_max_tool_iterations") or 8)
    sources: list[dict[str, Any]] = []
    collected = ""

    for _ in range(max_iter):
        tool_acc: dict[int, dict[str, Any]] = {}
        async for delta in chat_stream(
            session, messages, tools=OPENAI_TOOLS if tools_enabled else None
        ):
            if delta.get("content"):
                collected += delta["content"]
                yield {"type": "token", "content": delta["content"]}
            for call in delta.get("tool_calls") or []:
                bucket = tool_acc.setdefault(call["index"], {"id": "", "name": "", "arguments": ""})
                if call.get("id"):
                    bucket["id"] = call["id"]
                if call.get("name"):
                    bucket["name"] = call["name"]
                bucket["arguments"] += call.get("arguments") or ""

        if not tool_acc:
            break

        messages.append({"role": "assistant", "content": collected or None, "tool_calls": [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"]},
            }
            for c in tool_acc.values()
        ]})
        for call in tool_acc.values():
            started = datetime.now()
            try:
                args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool", "name": call["name"], "arguments": args}
            try:
                result = await dispatch(session, call["name"], args)
                error = None
            except Exception as exc:
                result, error = None, str(exc)
            duration = int((datetime.now() - started).total_seconds() * 1000)
            session.add(
                AgentToolCall(
                    message_id=assistant.id,
                    call_id=call["id"],
                    tool_name=call["name"],
                    arguments=args,
                    result=result,
                    error=error,
                    duration_ms=duration,
                    created_at=datetime.now(),
                )
            )
            if call["name"] == "search_records" and isinstance(result, list):
                sources.extend(
                    {"content_item_id": hit.get("content_item_id"), "chunk_id": hit.get("chunk_id")}
                    for hit in result
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result if error is None else {"error": error}, ensure_ascii=False, default=str),
                }
            )
            yield {"type": "tool_result", "name": call["name"], "error": error}
        collected = ""
        tools_enabled = True

    assistant.content = collected or assistant.content
    assistant.sources = sources
    await session.flush()
    yield {"type": "done", "message_id": assistant.id, "sources": sources}
