from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class QQPostOut(BaseModel):
    id: int
    qq_number: str
    post_id: str
    author_nickname: Optional[str] = None
    author_avatar: Optional[str] = None
    content: Optional[str] = None
    images: list = []
    post_time: Optional[datetime] = None
    like_count: int = 0
    comment_count: int = 0
    forward_content: Optional[str] = None
    crawled_at: Optional[datetime] = None
    comments: list = []

    class Config:
        from_attributes = True


class QQCommentOut(BaseModel):
    id: int
    author_nickname: Optional[str] = None
    author_avatar: Optional[str] = None
    content: Optional[str] = None
    comment_time: Optional[datetime] = None
    replies: list = []

    class Config:
        from_attributes = True


class CrawlRequest(BaseModel):
    account_ids: List[str]  # QQ号列表
    mode: str = "incremental"  # "incremental" or "overwrite"
    login_account_id: Optional[int] = None


class XHSNoteOut(BaseModel):
    id: int
    xhs_uid: str
    note_id: str
    author_nickname: Optional[str] = None
    author_avatar: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    images: list = []
    video_url: Optional[str] = None
    tags: list = []
    like_count: int = 0
    collect_count: int = 0
    comment_count: int = 0
    post_time: Optional[datetime] = None
    note_url: Optional[str] = None
    crawled_at: Optional[datetime] = None
    comments: list = []

    class Config:
        from_attributes = True


class XHSCrawlRequest(BaseModel):
    user_ids: List[str]
    mode: str = "incremental"  # "incremental" or "overwrite"
    login_account_id: Optional[int] = None


class AIConfigCreate(BaseModel):
    name: str
    api_base: str
    api_key: str
    model: str
    embed_model: Optional[str] = None
    max_tokens: int = 4096
    temperature: int = 7


class AIConfigUpdate(BaseModel):
    name: str
    api_base: str
    api_key: Optional[str] = None
    model: str
    embed_model: Optional[str] = None
    max_tokens: int = 4096
    temperature: int = 7


class ChatMessageCreate(BaseModel):
    content: str


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    sources: list = []
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChatSessionOut(BaseModel):
    id: int
    title: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AITaskCreate(BaseModel):
    name: str
    description: str
    target_qq: Optional[str] = "admin"  # "admin"=管理员QQ，或指定QQ号
    task_type: Optional[str] = None  # temporal/recurring/oneoff，未传时由后端决定或保留原值
    cron_expr: Optional[str] = None
    interval_minutes: Optional[int] = None
    message_format: str = "text_image"


class AITaskOut(BaseModel):
    id: int
    name: str
    description: str
    target_qq: Optional[str] = "admin"
    task_type: str = "recurring"
    cron_expr: Optional[str] = None
    interval_minutes: Optional[int] = None
    ai_suggested_interval: Optional[int] = None
    message_format: str = "text_image"
    is_active: bool = True
    last_run: Optional[datetime] = None
    last_triggered: Optional[datetime] = None
    last_checked_until: Optional[datetime] = None
    run_count: int = 0
    trigger_count: int = 0
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TaskLogOut(BaseModel):
    id: int
    task_id: int
    run_at: Optional[datetime] = None
    triggered: bool = False
    ai_reason: Optional[str] = None
    message_sent: Optional[str] = None
    error: Optional[str] = None
    confirmed: bool = False
    confirmed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SystemConfigItem(BaseModel):
    key: str
    value: Optional[str] = None
    description: Optional[str] = None


class SystemConfigOut(BaseModel):
    id: int
    key: str
    value: Optional[str] = None
    description: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
