"""add video_url/local_video_path to qq_posts; add local_video_path to xhs_notes

Revision ID: 007_add_video_fields
Revises: 006_add_comment_fields
Create Date: 2026-02-28
"""
from alembic import op
import sqlalchemy as sa

revision = '007_add_video_fields'
down_revision = '006_add_comment_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # QQ说说: 添加视频字段
    op.add_column('qq_posts', sa.Column('video_url', sa.String(500), nullable=True))
    op.add_column('qq_posts', sa.Column('local_video_path', sa.String(500), nullable=True))
    # 小红书笔记: 添加本地视频路径
    op.add_column('xhs_notes', sa.Column('local_video_path', sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column('xhs_notes', 'local_video_path')
    op.drop_column('qq_posts', 'local_video_path')
    op.drop_column('qq_posts', 'video_url')
