from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.agent.runtime import run_turn
from app.api.deps import AdminDep, SessionDep
from app.core.db import session_scope
from app.core.errors import NotFoundError
from app.domain.ops import AgentMessage, AgentSession, AgentToolCall

router = APIRouter()


class SessionCreate(BaseModel):
    title: str = "新对话"


class MessageCreate(BaseModel):
    content: str


@router.get("/sessions")
async def list_sessions(session: SessionDep, _: AdminDep):
    rows = (
        (await session.execute(select(AgentSession).order_by(AgentSession.updated_at.desc())))
        .scalars()
        .all()
    )
    return {"items": [{"id": r.id, "title": r.title, "updated_at": r.updated_at} for r in rows]}


@router.post("/sessions")
async def create_session(payload: SessionCreate, session: SessionDep, _: AdminDep):
    row = AgentSession(title=payload.title)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "title": row.title}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(AgentSession, session_id)
    if row is None:
        raise NotFoundError("会话不存在")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: int, session: SessionDep, _: AdminDep):
    rows = (
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
    return {
        "items": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources,
                "created_at": m.created_at,
            }
            for m in rows
        ]
    }


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: int, payload: MessageCreate, session: SessionDep, _: AdminDep):
    chat = await session.get(AgentSession, session_id)
    if chat is None:
        raise NotFoundError("会话不存在")

    async def event_stream():
        async with session_scope() as worker:
            async for event in run_turn(worker, session_id, payload.content):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
