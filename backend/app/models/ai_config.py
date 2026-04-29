from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, func
from app.database import Base


class AIConfig(Base):
    __tablename__ = "ai_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    api_base = Column(String(500), nullable=False)
    api_key = Column(Text, nullable=False)
    model = Column(String(100), nullable=False)
    embed_model = Column(String(100))
    max_tokens = Column(Integer, default=4096)
    temperature = Column(Integer, default=7)  # 存储为 0-10，使用时 /10
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
