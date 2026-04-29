"""add note_type, ip_location, video_duration, at_user_list to xhs_notes

Revision ID: 008_add_xhs_detail_fields
Revises: 007_add_video_fields
Create Date: 2025-07-11
"""
from alembic import op
import sqlalchemy as sa

revision = '008_add_xhs_detail_fields'
down_revision = '007_add_video_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('xhs_notes', sa.Column('note_type', sa.String(20), nullable=True))
    op.add_column('xhs_notes', sa.Column('ip_location', sa.String(100), nullable=True))
    op.add_column('xhs_notes', sa.Column('video_duration', sa.Integer(), nullable=True))
    op.add_column('xhs_notes', sa.Column('at_user_list', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('xhs_notes', 'at_user_list')
    op.drop_column('xhs_notes', 'video_duration')
    op.drop_column('xhs_notes', 'ip_location')
    op.drop_column('xhs_notes', 'note_type')
