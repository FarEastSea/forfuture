from sqlalchemy import Column, Integer, String, Text, DateTime, func
from legacy.database import Base


class SystemConfig(Base):
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), nullable=False, unique=True)
    value = Column(Text)
    description = Column(String(500))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
