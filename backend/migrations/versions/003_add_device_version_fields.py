"""Add device_info, location, edit_history, platform_uid, signature fields

Revision ID: 003_add_device_version_fields
Revises: 002_admin_temporal
Create Date: 2026-02-26

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '003_add_device_version_fields'
down_revision: Union[str, None] = '002_admin_temporal'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # accounts: platform_uid + signature
    op.add_column('accounts', sa.Column('platform_uid', sa.String(200)))
    op.add_column('accounts', sa.Column('signature', sa.String(500)))

    # qq_posts: device_info, location, edit_history, updated_at
    op.add_column('qq_posts', sa.Column('device_info', sa.String(200)))
    op.add_column('qq_posts', sa.Column('location', sa.String(200)))
    op.add_column('qq_posts', sa.Column('edit_history', sa.JSON(), server_default='[]'))
    op.add_column('qq_posts', sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()))

    # xhs_notes: device_info, location, edit_history, share_count, last_update_time, updated_at
    op.add_column('xhs_notes', sa.Column('device_info', sa.String(200)))
    op.add_column('xhs_notes', sa.Column('location', sa.String(200)))
    op.add_column('xhs_notes', sa.Column('edit_history', sa.JSON(), server_default='[]'))
    op.add_column('xhs_notes', sa.Column('share_count', sa.Integer(), server_default='0'))
    op.add_column('xhs_notes', sa.Column('last_update_time', sa.DateTime()))
    op.add_column('xhs_notes', sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()))


def downgrade() -> None:
    op.drop_column('xhs_notes', 'updated_at')
    op.drop_column('xhs_notes', 'last_update_time')
    op.drop_column('xhs_notes', 'share_count')
    op.drop_column('xhs_notes', 'edit_history')
    op.drop_column('xhs_notes', 'location')
    op.drop_column('xhs_notes', 'device_info')

    op.drop_column('qq_posts', 'updated_at')
    op.drop_column('qq_posts', 'edit_history')
    op.drop_column('qq_posts', 'location')
    op.drop_column('qq_posts', 'device_info')

    op.drop_column('accounts', 'signature')
    op.drop_column('accounts', 'platform_uid')
