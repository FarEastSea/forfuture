"""add account risk fields

Revision ID: 011_add_account_risk_fields
Revises: 010_add_post_summaries
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa


revision = "011_add_account_risk_fields"
down_revision = "010_add_post_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("cookie_last_validated_at", sa.DateTime(), nullable=True))
    op.add_column("accounts", sa.Column("last_cookie_refresh_at", sa.DateTime(), nullable=True))
    op.add_column("accounts", sa.Column("last_failure_at", sa.DateTime(), nullable=True))
    op.add_column("accounts", sa.Column("last_failure_reason", sa.String(length=500), nullable=True))
    op.add_column("accounts", sa.Column("risk_cooldown_until", sa.DateTime(), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
    )

    accounts_table = sa.table(
        "accounts",
        sa.column("is_target", sa.Integer()),
        sa.column("last_login", sa.DateTime()),
        sa.column("cookie_last_validated_at", sa.DateTime()),
        sa.column("last_cookie_refresh_at", sa.DateTime()),
        sa.column("failure_count", sa.Integer()),
    )
    op.execute(
        accounts_table.update()
        .where(accounts_table.c.is_target == 0)
        .where(accounts_table.c.last_login.isnot(None))
        .values(
            cookie_last_validated_at=accounts_table.c.last_login,
            last_cookie_refresh_at=accounts_table.c.last_login,
            failure_count=0,
        )
    )


def downgrade() -> None:
    op.drop_column("accounts", "failure_count")
    op.drop_column("accounts", "risk_cooldown_until")
    op.drop_column("accounts", "last_failure_reason")
    op.drop_column("accounts", "last_failure_at")
    op.drop_column("accounts", "last_cookie_refresh_at")
    op.drop_column("accounts", "cookie_last_validated_at")