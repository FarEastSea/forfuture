"""Add admin, temporal task, confirmation fields

Revision ID: 002_admin_temporal
Revises: 001_initial
Create Date: 2026-02-23

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_admin_temporal'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ai_tasks: 时效性任务 + 智能调度
    op.add_column('ai_tasks', sa.Column('task_type', sa.String(20), server_default='recurring'))
    op.add_column('ai_tasks', sa.Column('ai_suggested_interval', sa.Integer()))
    op.add_column('ai_tasks', sa.Column('last_checked_until', sa.DateTime()))

    # ai_tasks: target_qq 改为可空, 默认 'admin'
    op.alter_column('ai_tasks', 'target_qq', nullable=True, server_default='admin')

    # task_logs: 确认追踪
    op.add_column('task_logs', sa.Column('confirmed', sa.Boolean(), server_default='false'))
    op.add_column('task_logs', sa.Column('confirmed_at', sa.DateTime()))


def downgrade() -> None:
    op.drop_column('task_logs', 'confirmed_at')
    op.drop_column('task_logs', 'confirmed')
    op.alter_column('ai_tasks', 'target_qq', nullable=False, server_default=None)
    op.drop_column('ai_tasks', 'last_checked_until')
    op.drop_column('ai_tasks', 'ai_suggested_interval')
    op.drop_column('ai_tasks', 'task_type')
