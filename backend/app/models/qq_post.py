from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, func, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class QQPost(Base):
    __tablename__ = "qq_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    qq_number = Column(String(20), nullable=False, index=True)
    post_id = Column(String(100), nullable=False, unique=True)
    author_qq = Column(String(20))
    author_nickname = Column(String(100))
    author_avatar = Column(String(500))
    content = Column(Text)
    images = Column(JSON, default=list)
    video_url = Column(String(500))
    local_video_path = Column(String(500))
    post_time = Column(DateTime, index=True)
    like_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    forward_content = Column(Text)
    device_info = Column(String(200))  # 发布设备，如 "荣耀90 GT (5G)"
    location = Column(String(200))  # 位置信息
    edit_history = Column(JSON, default=list)  # 编辑历史记录
    raw_data = Column(JSON)
    crawled_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    comments = relationship("QQComment", back_populates="post", cascade="all, delete-orphan")


class QQComment(Base):
    __tablename__ = "qq_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey("qq_posts.id", ondelete="CASCADE"), nullable=False)
    comment_id = Column(String(100))
    author_qq = Column(String(20))
    author_nickname = Column(String(100))
    author_avatar = Column(String(500))
    content = Column(Text)
    comment_time = Column(DateTime)
    reply_to_id = Column(Integer, ForeignKey("qq_comments.id"))
    created_at = Column(DateTime, server_default=func.now())

    post = relationship("QQPost", back_populates="comments")
    replies = relationship("QQComment", backref="reply_to", remote_side=[id])
