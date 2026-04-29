"""add author_avatar, like_count, target_nickname, sub_comment_count to xhs_comments; make comment_id unique

Revision ID: 006_add_comment_fields
Revises: 005_add_nickname_history
Create Date: 2026-02-28
"""
from alembic import op
import sqlalchemy as sa

revision = '006_add_comment_fields'
down_revision = '005_add_nickname_history'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('xhs_comments', sa.Column('author_avatar', sa.String(500), nullable=True))
    op.add_column('xhs_comments', sa.Column('like_count', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('xhs_comments', sa.Column('target_nickname', sa.String(100), nullable=True))
    op.add_column('xhs_comments', sa.Column('sub_comment_count', sa.Integer(), nullable=True, server_default='0'))
    # Make comment_id unique (drop existing index if any, then create unique)
    try:
        op.create_index('ix_xhs_comments_comment_id', 'xhs_comments', ['comment_id'], unique=True)
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.drop_index('ix_xhs_comments_comment_id', table_name='xhs_comments')
    except Exception:
        pass
    op.drop_column('xhs_comments', 'sub_comment_count')
    op.drop_column('xhs_comments', 'target_nickname')
    op.drop_column('xhs_comments', 'like_count')
    op.drop_column('xhs_comments', 'author_avatar')
