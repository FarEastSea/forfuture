"""统一内容模型：身份 / 内容 / 媒体 / 知识库 / 运维

只创建新表，完全不触碰 accounts / qq_posts / xhs_notes 等 legacy 表——那些表在数据
搬运并核对通过之前必须原封不动继续服务旧接口。

pgvector 若可用则额外建 vector 列与 HNSW 索引；不可用时嵌入向量退化为 BYTEA 存储，
检索层会自动选路，因此扩展缺失不影响功能，只影响大数据量下的检索速度。

Revision ID: 012_unified_content_model
Revises: 011_add_account_risk_fields
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "012_unified_content_model"
down_revision = "011_add_account_risk_fields"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    ]


def _pgvector_available(bind) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ 身份
    op.create_table(
        "proxies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("scheme", sa.String(20), nullable=False, server_default="http"),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("password", sa.String(255)),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_checked_at", sa.DateTime()),
        sa.Column("last_ok_at", sa.DateTime()),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text()),
        *_timestamps(),
        sa.UniqueConstraint("host", "port", "username", name="uq_proxies_endpoint"),
    )

    op.create_table(
        "browser_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("platform", sa.String(20)),
        sa.Column("fingerprint", JSONB, nullable=False, server_default="{}"),
        sa.Column("profile_dir", sa.String(500), nullable=False),
        sa.Column("last_used_at", sa.DateTime()),
        sa.Column("notes", sa.Text()),
        *_timestamps(),
    )

    # media_assets 要先于引用它的表建立
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("platform", sa.String(20)),
        sa.Column("remote_url", sa.Text()),
        sa.Column("local_path", sa.String(500)),
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("byte_size", sa.BigInteger()),
        sa.Column("mime_type", sa.String(120)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("download_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("downloaded_at", sa.DateTime()),
        sa.Column("last_verified_at", sa.DateTime()),
        sa.Column("fallback_urls", JSONB, nullable=False, server_default="[]"),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_index(
        "uq_media_assets_local_path",
        "media_assets",
        ["local_path"],
        unique=True,
        postgresql_where=sa.text("local_path IS NOT NULL"),
    )
    op.create_index(
        "ix_media_assets_sha256",
        "media_assets",
        ["content_sha256"],
        postgresql_where=sa.text("content_sha256 IS NOT NULL"),
    )
    op.create_index("ix_media_assets_kind_status", "media_assets", ["kind", "status"])

    op.create_table(
        "platform_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("account_id", sa.String(100), nullable=False),
        sa.Column("platform_uid", sa.String(200)),
        sa.Column("nickname", sa.String(200)),
        sa.Column("signature", sa.Text()),
        sa.Column("avatar_media_id", sa.Integer()),
        sa.Column("avatar_url", sa.String(1000)),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_crawled_at", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("notes", sa.Text()),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["avatar_media_id"],
            ["media_assets.id"],
            name="fk_platform_accounts_avatar_media_id_media_assets",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "platform", "account_id", name="uq_platform_accounts_platform_account_id"
        ),
    )
    op.create_index(
        "ix_platform_accounts_platform_enabled", "platform_accounts", ["platform", "is_enabled"]
    )

    op.create_table(
        "account_profile_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["platform_accounts.id"],
            name="fk_account_profile_history_account_id_platform_accounts",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_account_profile_history_account_field",
        "account_profile_history",
        ["account_id", "field"],
    )

    op.create_table(
        "login_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("account_label", sa.String(100), nullable=False),
        sa.Column("platform_uid", sa.String(200)),
        sa.Column("nickname", sa.String(200)),
        sa.Column("cookies", JSONB, nullable=False, server_default="[]"),
        sa.Column("storage_state", JSONB),
        sa.Column("token", sa.Text()),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failure_at", sa.DateTime()),
        sa.Column("last_failure_reason", sa.Text()),
        sa.Column("cooldown_until", sa.DateTime()),
        sa.Column("last_validated_at", sa.DateTime()),
        sa.Column("last_refreshed_at", sa.DateTime()),
        sa.Column("last_login_at", sa.DateTime()),
        sa.Column("last_used_at", sa.DateTime()),
        sa.Column("browser_profile_id", sa.Integer()),
        sa.Column("proxy_id", sa.Integer()),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["browser_profile_id"],
            ["browser_profiles.id"],
            name="fk_login_credentials_browser_profile_id_browser_profiles",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["proxy_id"], ["proxies.id"], name="fk_login_credentials_proxy_id_proxies", ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "platform", "account_label", name="uq_login_credentials_platform_account_label"
        ),
    )
    op.create_index(
        "ix_login_credentials_platform_status", "login_credentials", ["platform", "status"]
    )

    # ------------------------------------------------------------------ 内容
    op.create_table(
        "content_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("platform_item_id", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(30), nullable=False),
        sa.Column("target_account_id", sa.Integer()),
        sa.Column("author_platform_uid", sa.String(200)),
        sa.Column("author_name", sa.String(200)),
        sa.Column("author_avatar_media_id", sa.Integer()),
        sa.Column("title", sa.Text()),
        sa.Column("body", sa.Text()),
        sa.Column("posted_at", sa.DateTime()),
        sa.Column("edited_at", sa.DateTime()),
        sa.Column("ip_location", sa.String(100)),
        sa.Column("geo_location", sa.String(300)),
        sa.Column("device", sa.String(200)),
        sa.Column("source_url", sa.Text()),
        sa.Column("metrics", JSONB, nullable=False, server_default="{}"),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
        sa.Column("raw", JSONB),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("first_seen_at", sa.DateTime()),
        sa.Column("last_seen_at", sa.DateTime()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["target_account_id"],
            ["platform_accounts.id"],
            name="fk_content_items_target_account_id_platform_accounts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["author_avatar_media_id"],
            ["media_assets.id"],
            name="fk_content_items_author_avatar_media_id_media_assets",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("platform", "platform_item_id", name="uq_content_items_platform_item"),
    )
    op.create_index(
        "ix_content_items_platform_posted_at", "content_items", ["platform", "posted_at"]
    )
    op.create_index(
        "ix_content_items_target_posted_at", "content_items", ["target_account_id", "posted_at"]
    )
    op.create_index("ix_content_items_content_hash", "content_items", ["content_hash"])

    op.create_table(
        "content_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_item_id", sa.Integer(), nullable=False),
        sa.Column("platform_comment_id", sa.String(200), nullable=False),
        sa.Column("parent_id", sa.Integer()),
        sa.Column("author_platform_uid", sa.String(200)),
        sa.Column("author_name", sa.String(200)),
        sa.Column("author_avatar_media_id", sa.Integer()),
        sa.Column("body", sa.Text()),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sub_comment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("commented_at", sa.DateTime()),
        sa.Column("ip_location", sa.String(100)),
        sa.Column("reply_to_name", sa.String(200)),
        sa.Column("is_author_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw", JSONB),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name="fk_content_comments_content_item_id_content_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["content_comments.id"],
            name="fk_content_comments_parent_id_content_comments",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_avatar_media_id"],
            ["media_assets.id"],
            name="fk_content_comments_author_avatar_media_id_media_assets",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "content_item_id", "platform_comment_id", name="uq_content_comments_item_comment"
        ),
    )
    op.create_index(
        "ix_content_comments_item_time", "content_comments", ["content_item_id", "commented_at"]
    )

    op.create_table(
        "content_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_item_id", sa.Integer(), nullable=False),
        sa.Column("revision_index", sa.Integer(), nullable=False),
        sa.Column("changed_fields", JSONB, nullable=False, server_default="{}"),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name="fk_content_revisions_content_item_id_content_items",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "content_item_id", "revision_index", name="uq_content_revisions_item_index"
        ),
    )

    op.create_table(
        "content_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_item_id", sa.Integer(), nullable=False),
        sa.Column("media_asset_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name="fk_content_media_content_item_id_content_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name="fk_content_media_media_asset_id_media_assets",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "content_item_id", "media_asset_id", "role", name="uq_content_media_item_asset_role"
        ),
    )
    op.create_index(
        "ix_content_media_item_position", "content_media", ["content_item_id", "position"]
    )

    # ---------------------------------------------------------------- 知识库
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_item_id", sa.Integer()),
        sa.Column("chunk_type", sa.String(30), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", sa.LargeBinary()),
        sa.Column("embedding_model", sa.String(120)),
        sa.Column("embedding_dim", sa.Integer()),
        sa.Column("lexemes", postgresql.TSVECTOR()),
        sa.Column("platform", sa.String(20)),
        sa.Column("author_name", sa.String(200)),
        sa.Column("posted_at", sa.DateTime()),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name="fk_knowledge_chunks_content_item_id_content_items",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "content_item_id",
            "chunk_type",
            "chunk_index",
            name="uq_knowledge_chunks_item_type_index",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_lexemes", "knowledge_chunks", ["lexemes"], postgresql_using="gin"
    )
    op.create_index(
        "ix_knowledge_chunks_platform_posted", "knowledge_chunks", ["platform", "posted_at"]
    )
    op.create_index("ix_knowledge_chunks_source_hash", "knowledge_chunks", ["source_hash"])

    # ------------------------------------------------------------------ 运维
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime()),
    )

    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("target_account_ids", JSONB),
        sa.Column("credential_id", sa.Integer()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("requested_by", sa.String(60)),
        sa.Column("progress", JSONB, nullable=False, server_default="{}"),
        sa.Column("stats", JSONB, nullable=False, server_default="{}"),
        sa.Column("checkpoint", JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("queued_at", sa.DateTime()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("heartbeat_at", sa.DateTime()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["login_credentials.id"],
            name="fk_crawl_jobs_credential_id_login_credentials",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_crawl_jobs_status_priority", "crawl_jobs", ["status", "priority", "id"])
    op.create_index("ix_crawl_jobs_platform_created", "crawl_jobs", ["platform", "created_at"])

    op.create_table(
        "crawl_job_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", JSONB),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["job_id"], ["crawl_jobs.id"], name="fk_crawl_job_events_job_id_crawl_jobs", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_crawl_job_events_job_created", "crawl_job_events", ["job_id", "created_at"])

    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("schedule_kind", sa.String(20), nullable=False),
        sa.Column("cron_expr", sa.String(120)),
        sa.Column("interval_seconds", sa.Integer()),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime()),
        sa.Column("next_run_at", sa.DateTime()),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
    )

    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("triggered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text()),
        sa.Column("output", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["scheduled_tasks.id"],
            name="fk_task_runs_task_id_scheduled_tasks",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_task_runs_task_started", "task_runs", ["task_id", "started_at"])

    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("api_base", sa.String(500), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("chat_model", sa.String(200), nullable=False),
        sa.Column("embed_model", sa.String(200)),
        sa.Column("vision_model", sa.String(200)),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="4096"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
    )

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False, server_default="新对话"),
        *_timestamps(),
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("sources", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name="fk_agent_messages_session_id_agent_sessions",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_agent_messages_session_created", "agent_messages", ["session_id", "created_at"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(120)),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("arguments", JSONB, nullable=False, server_default="{}"),
        sa.Column("result", JSONB),
        sa.Column("error", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["agent_messages.id"],
            name="fk_agent_tool_calls_message_id_agent_messages",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "migration_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_table", sa.String(80), nullable=False),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("new_table", sa.String(80), nullable=False),
        sa.Column("new_id", sa.Integer(), nullable=False),
        sa.Column("migrated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "legacy_table", "legacy_id", "new_table", name="uq_migration_audit_legacy_new"
        ),
    )

    # ------------------------------------------------- pgvector（可用才启用）
    if _pgvector_available(bind):
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        dim = _embedding_dimensions()
        op.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN embedding_vec vector({dim})")
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_embedding_vec ON knowledge_chunks "
            "USING hnsw (embedding_vec vector_cosine_ops)"
        )


def _embedding_dimensions() -> int:
    try:
        from app.core.config import settings

        return int(settings.embedding_dimensions)
    except Exception:
        return 1536


def downgrade() -> None:
    for table in (
        "migration_audit",
        "agent_tool_calls",
        "agent_messages",
        "agent_sessions",
        "llm_providers",
        "task_runs",
        "scheduled_tasks",
        "crawl_job_events",
        "crawl_jobs",
        "app_settings",
        "knowledge_chunks",
        "content_media",
        "content_revisions",
        "content_comments",
        "content_items",
        "login_credentials",
        "account_profile_history",
        "platform_accounts",
        "media_assets",
        "browser_profiles",
        "proxies",
    ):
        op.drop_table(table)
