"""Add avatar_history to accounts

Revision ID: 004_add_avatar_history
Revises: 003_add_device_version_fields
Create Date: 2025-02-27

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004_add_avatar_history'
down_revision: Union[str, None] = '003_add_device_version_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('accounts', sa.Column('avatar_history', sa.JSON(), server_default='[]'))


def downgrade() -> None:
    op.drop_column('accounts', 'avatar_history')
