from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, func, ForeignKey
from sqlalchemy.orm import relationship
from legacy.database import Base


class XHSNote(Base):
    __tablename__ = "xhs_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    xhs_uid = Column(String(100), nullable=False, index=True)
    note_id = Column(String(100), nullable=False, unique=True)
    note_type = Column(String(20))  # "video" / "normal"(图文)
    author_uid = Column(String(100))
    author_nickname = Column(String(100))
    author_avatar = Column(String(500))
    title = Column(String(500))
    content = Column(Text)
    images = Column(JSON, default=list)
    video_url = Column(String(500))
    local_video_path = Column(String(500))
    video_duration = Column(Integer)  # 视频时长（秒）
    tags = Column(JSON, default=list)
    at_user_list = Column(JSON, default=list)  # @用户列表
    like_count = Column(Integer, default=0)
    collect_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
    post_time = Column(DateTime, index=True)
    last_update_time = Column(DateTime)  # 笔记最后修改时间
    note_url = Column(String(500))
    ip_location = Column(String(100))  # IP属地（如"江苏"）
    location = Column(String(200))  # 位置信息
    device_info = Column(String(200))  # 发布设备
    edit_history = Column(JSON, default=list)  # 编辑历史记录
    raw_data = Column(JSON)
    crawled_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    comments = relationship("XHSComment", back_populates="note", cascade="all, delete-orphan")


class XHSComment(Base):
    __tablename__ = "xhs_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(Integer, ForeignKey("xhs_notes.id", ondelete="CASCADE"), nullable=False)
    comment_id = Column(String(100), unique=True)
    author_uid = Column(String(100))
    author_nickname = Column(String(100))
    author_avatar = Column(String(500))
    content = Column(Text)
    like_count = Column(Integer, default=0)
    comment_time = Column(DateTime)
    reply_to_id = Column(Integer, ForeignKey("xhs_comments.id"))
    target_nickname = Column(String(100))  # 被回复人昵称
    sub_comment_count = Column(Integer, default=0)
    ip_location = Column(String(100))  # IP属地
    is_author = Column(Integer, default=0)  # 是否作者 1=是
    created_at = Column(DateTime, server_default=func.now())

    note = relationship("XHSNote", back_populates="comments")
    replies = relationship("XHSComment", backref="reply_to", remote_side=[id])
