from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, func, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    sources = Column(JSON, default=[])  # [{type, id, snippet}]
    created_at = Column(DateTime, server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")
