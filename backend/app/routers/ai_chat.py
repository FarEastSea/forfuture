from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi.responses import StreamingResponse
from app.database import get_db
from app.models.chat_history import ChatSession, ChatMessage
from app.schemas import ChatMessageCreate, ChatSessionOut

router = APIRouter()


@router.get("/sessions")
async def get_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChatSession).order_by(ChatSession.updated_at.desc()))
    sessions = result.scalars().all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in sessions
    ]


@router.post("/sessions")
async def create_session(db: AsyncSession = Depends(get_db)):
    session = ChatSession(title="新对话")
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {"id": session.id, "title": session.title}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    await db.delete(session)
    await db.commit()
    return {"message": "删除成功"}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    messages = result.scalars().all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "sources": m.sources or [],
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: int, req: ChatMessageCreate, db: AsyncSession = Depends(get_db)):
    # 验证会话存在
    sess_result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = sess_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 保存用户消息
    user_msg = ChatMessage(session_id=session_id, role="user", content=req.content)
    db.add(user_msg)
    await db.commit()

    # 更新会话标题（首条消息时）
    if not session.title or session.title == "新对话":
        session.title = req.content[:50]
        await db.commit()

    # 获取历史消息
    history_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    history = history_result.scalars().all()

    from app.services.ai_service import ai_service

    async def generate():
        full_response = ""
        sources = []
        context_stats = None
        try:
            async for chunk_data in ai_service.chat_stream(req.content, history, session_id):
                if isinstance(chunk_data, dict):
                    sources = chunk_data.get("sources", [])
                    context_stats = chunk_data.get("context_stats")
                    # 源引用太多时只保留前20条（全量已在上下文中）
                    if len(sources) > 20:
                        sources = sources[:20]
                else:
                    full_response += chunk_data
                    yield f"data: {chunk_data}\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"
            full_response = f"[错误] {str(e)}"

        # 保存AI回复
        async with get_db_session() as save_db:
            ai_msg = ChatMessage(
                session_id=session_id,
                role="assistant",
                content=full_response,
                sources=sources,
            )
            save_db.add(ai_msg)
            await save_db.commit()

        # 发送上下文统计信息
        if context_stats:
            import json
            yield f"data: [CONTEXT_STATS]{json.dumps(context_stats)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


async def get_db_session():
    from app.database import async_session
    return async_session()
