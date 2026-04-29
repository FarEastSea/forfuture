from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, func
from app.database import Base


class PostSummary(Base):
    """动态/笔记的AI生成总结，用于智能上下文模式"""
    __tablename__ = "post_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, index=True)  # "qq" or "xhs"
    post_db_id = Column(Integer, nullable=False, index=True)    # QQPost.id or XHSNote.id
    post_original_id = Column(String(100))  # post_id / note_id (原始平台ID)
    author = Column(String(100))
    post_time = Column(DateTime, index=True)  # 原动态发布时间
    summary = Column(Text, nullable=False)    # AI生成的总结
    images_count = Column(Integer, default=0)
    has_video = Column(Boolean, default=False)
    # 用于判断是否需要重新总结
    source_updated_at = Column(DateTime)      # 原动态最后更新时间
    summary_version = Column(Integer, default=1)  # 总结版本号，每次重新总结+1
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
