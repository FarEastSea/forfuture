"""add nickname_history field

Revision ID: 005_add_nickname_history
Revises: 004_add_avatar_history
Create Date: 2026-02-27
"""
from alembic import op
import sqlalchemy as sa

revision = '005_add_nickname_history'
down_revision = '004_add_avatar_history'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('accounts', sa.Column('nickname_history', sa.JSON(), nullable=True, server_default='[]'))


def downgrade() -> None:
    op.drop_column('accounts', 'nickname_history')
