"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-02-23

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # accounts
    op.create_table(
        'accounts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('platform', sa.String(20), nullable=False),
        sa.Column('account_id', sa.String(100), nullable=False),
        sa.Column('nickname', sa.String(100)),
        sa.Column('avatar_url', sa.String(500)),
        sa.Column('cookies', sa.Text()),
        sa.Column('token', sa.Text()),
        sa.Column('status', sa.String(20), server_default='active'),
        sa.Column('last_login', sa.DateTime()),
        sa.Column('is_target', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # qq_posts
    op.create_table(
        'qq_posts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('qq_number', sa.String(20), nullable=False),
        sa.Column('post_id', sa.String(100), nullable=False, unique=True),
        sa.Column('author_qq', sa.String(20)),
        sa.Column('author_nickname', sa.String(100)),
        sa.Column('author_avatar', sa.String(500)),
        sa.Column('content', sa.Text()),
        sa.Column('images', sa.JSON(), server_default='[]'),
        sa.Column('post_time', sa.DateTime()),
        sa.Column('like_count', sa.Integer(), server_default='0'),
        sa.Column('comment_count', sa.Integer(), server_default='0'),
        sa.Column('forward_content', sa.Text()),
        sa.Column('raw_data', sa.JSON()),
        sa.Column('crawled_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('idx_qq_posts_qq_number', 'qq_posts', ['qq_number'])
    op.create_index('idx_qq_posts_post_time', 'qq_posts', ['post_time'])

    # qq_comments
    op.create_table(
        'qq_comments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('post_id', sa.Integer(), sa.ForeignKey('qq_posts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('comment_id', sa.String(100)),
        sa.Column('author_qq', sa.String(20)),
        sa.Column('author_nickname', sa.String(100)),
        sa.Column('author_avatar', sa.String(500)),
        sa.Column('content', sa.Text()),
        sa.Column('comment_time', sa.DateTime()),
        sa.Column('reply_to_id', sa.Integer(), sa.ForeignKey('qq_comments.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # xhs_notes
    op.create_table(
        'xhs_notes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('xhs_uid', sa.String(100), nullable=False),
        sa.Column('note_id', sa.String(100), nullable=False, unique=True),
        sa.Column('author_uid', sa.String(100)),
        sa.Column('author_nickname', sa.String(100)),
        sa.Column('author_avatar', sa.String(500)),
        sa.Column('title', sa.String(500)),
        sa.Column('content', sa.Text()),
        sa.Column('images', sa.JSON(), server_default='[]'),
        sa.Column('video_url', sa.String(500)),
        sa.Column('tags', sa.JSON(), server_default='[]'),
        sa.Column('like_count', sa.Integer(), server_default='0'),
        sa.Column('collect_count', sa.Integer(), server_default='0'),
        sa.Column('comment_count', sa.Integer(), server_default='0'),
        sa.Column('post_time', sa.DateTime()),
        sa.Column('note_url', sa.String(500)),
        sa.Column('raw_data', sa.JSON()),
        sa.Column('crawled_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('idx_xhs_notes_xhs_uid', 'xhs_notes', ['xhs_uid'])
    op.create_index('idx_xhs_notes_post_time', 'xhs_notes', ['post_time'])

    # xhs_comments
    op.create_table(
        'xhs_comments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('note_id', sa.Integer(), sa.ForeignKey('xhs_notes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('comment_id', sa.String(100)),
        sa.Column('author_uid', sa.String(100)),
        sa.Column('author_nickname', sa.String(100)),
        sa.Column('content', sa.Text()),
        sa.Column('comment_time', sa.DateTime()),
        sa.Column('reply_to_id', sa.Integer(), sa.ForeignKey('xhs_comments.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # ai_configs
    op.create_table(
        'ai_configs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('api_base', sa.String(500), nullable=False),
        sa.Column('api_key', sa.Text(), nullable=False),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('embed_model', sa.String(100)),
        sa.Column('max_tokens', sa.Integer(), server_default='4096'),
        sa.Column('temperature', sa.Integer(), server_default='7'),
        sa.Column('is_active', sa.Boolean(), server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # chat_sessions
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('title', sa.String(200)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # chat_messages
    op.create_table(
        'chat_messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('chat_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('sources', sa.JSON(), server_default='[]'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('idx_chat_messages_session', 'chat_messages', ['session_id'])

    # ai_tasks
    op.create_table(
        'ai_tasks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('target_qq', sa.String(20), nullable=False),
        sa.Column('cron_expr', sa.String(100)),
        sa.Column('interval_minutes', sa.Integer()),
        sa.Column('message_format', sa.String(50), server_default='text_image'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('last_run', sa.DateTime()),
        sa.Column('last_triggered', sa.DateTime()),
        sa.Column('run_count', sa.Integer(), server_default='0'),
        sa.Column('trigger_count', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # task_logs
    op.create_table(
        'task_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('ai_tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('run_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('triggered', sa.Boolean(), server_default='false'),
        sa.Column('ai_reason', sa.Text()),
        sa.Column('message_sent', sa.Text()),
        sa.Column('error', sa.Text()),
    )
    op.create_index('idx_task_logs_task_id', 'task_logs', ['task_id'])

    # system_configs
    op.create_table(
        'system_configs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('key', sa.String(100), nullable=False, unique=True),
        sa.Column('value', sa.Text()),
        sa.Column('description', sa.String(500)),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('system_configs')
    op.drop_table('task_logs')
    op.drop_table('ai_tasks')
    op.drop_table('chat_messages')
    op.drop_table('chat_sessions')
    op.drop_table('ai_configs')
    op.drop_table('xhs_comments')
    op.drop_table('xhs_notes')
    op.drop_table('qq_comments')
    op.drop_table('qq_posts')
    op.drop_table('accounts')
