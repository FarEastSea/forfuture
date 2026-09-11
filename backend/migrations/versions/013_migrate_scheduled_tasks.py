"""Migrate legacy AI tasks into the Agent-driven scheduler.

Revision ID: 013_migrate_scheduled_tasks
Revises: 012_unified_content_model
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa

revision = "013_migrate_scheduled_tasks"
down_revision = "012_unified_content_model"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "ai_tasks"):
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO scheduled_tasks (
                id, name, description, kind, schedule_kind, cron_expr,
                interval_seconds, config, is_enabled, last_run_at,
                run_count, failure_count, created_at, updated_at
            )
            SELECT
                id,
                name,
                description,
                'agent',
                CASE WHEN NULLIF(BTRIM(cron_expr), '') IS NOT NULL THEN 'cron' ELSE 'interval' END,
                NULLIF(BTRIM(cron_expr), ''),
                CASE WHEN NULLIF(BTRIM(cron_expr), '') IS NULL
                     THEN GREATEST(COALESCE(interval_minutes, ai_suggested_interval, 120), 5) * 60
                     ELSE NULL END,
                jsonb_build_object(
                    'target_qq', COALESCE(target_qq, 'admin'),
                    'message_format', COALESCE(message_format, 'text'),
                    'legacy_task_type', COALESCE(task_type, 'recurring'),
                    'trigger_count', COALESCE(trigger_count, 0),
                    'last_triggered', last_triggered,
                    'last_checked_until', last_checked_until
                ),
                COALESCE(is_active, true),
                last_run,
                COALESCE(run_count, 0),
                0,
                COALESCE(created_at, now()),
                now()
            FROM ai_tasks
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            "SELECT setval(pg_get_serial_sequence('scheduled_tasks','id'), "
            "GREATEST(COALESCE((SELECT max(id) FROM scheduled_tasks), 1), 1), true)"
        )
    )

    if _table_exists(bind, "task_logs"):
        bind.execute(
            sa.text(
                """
                INSERT INTO task_runs (
                    id, task_id, status, triggered, reason, output, error,
                    started_at, finished_at
                )
                SELECT
                    id,
                    task_id,
                    CASE WHEN error IS NULL THEN 'succeeded' ELSE 'failed' END,
                    COALESCE(triggered, false),
                    ai_reason,
                    message_sent,
                    error,
                    COALESCE(run_at, now()),
                    COALESCE(run_at, now())
                FROM task_logs
                WHERE EXISTS (SELECT 1 FROM scheduled_tasks s WHERE s.id = task_logs.task_id)
                ON CONFLICT (id) DO NOTHING
                """
            )
        )
        bind.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence('task_runs','id'), "
                "GREATEST(COALESCE((SELECT max(id) FROM task_runs), 1), 1), true)"
            )
        )


def downgrade() -> None:
    # Legacy tables remain the archival source of truth; downgrading does not
    # delete migrated task history.
    pass
