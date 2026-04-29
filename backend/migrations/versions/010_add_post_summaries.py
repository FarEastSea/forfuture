"""add post_summaries table

Revision ID: 010_add_post_summaries
Revises: 009_add_comment_detail_fields
Create Date: 2026-03-05
"""
from alembic import op
import sqlalchemy as sa

revision = '010_add_post_summaries'
down_revision = '009_add_comment_detail_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'post_summaries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('platform', sa.String(20), nullable=False, index=True),
        sa.Column('post_db_id', sa.Integer(), nullable=False, index=True),
        sa.Column('post_original_id', sa.String(100), nullable=True),
        sa.Column('author', sa.String(100), nullable=True),
        sa.Column('post_time', sa.DateTime(), nullable=True, index=True),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('images_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('has_video', sa.Boolean(), nullable=True, server_default='false'),
        sa.Column('source_updated_at', sa.DateTime(), nullable=True),
        sa.Column('summary_version', sa.Integer(), nullable=True, server_default='1'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    # 唯一约束: 每条动态只有一条总结
    op.create_unique_constraint('uq_post_summary_platform_id', 'post_summaries', ['platform', 'post_db_id'])


def downgrade() -> None:
    op.drop_table('post_summaries')
