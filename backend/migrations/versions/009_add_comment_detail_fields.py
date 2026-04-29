"""add ip_location, is_author to xhs_comments

Revision ID: 009_add_comment_detail_fields
Revises: 008_add_xhs_detail_fields
Create Date: 2026-02-28
"""
from alembic import op
import sqlalchemy as sa

revision = '009_add_comment_detail_fields'
down_revision = '008_add_xhs_detail_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('xhs_comments', sa.Column('ip_location', sa.String(100), nullable=True))
    op.add_column('xhs_comments', sa.Column('is_author', sa.Integer(), nullable=True, server_default='0'))


def downgrade() -> None:
    op.drop_column('xhs_comments', 'is_author')
    op.drop_column('xhs_comments', 'ip_location')
