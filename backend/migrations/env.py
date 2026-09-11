"""Alembic 环境。

用 asyncpg 跑迁移，不再依赖 psycopg2：项目运行时本来就只装 asyncpg，多带一个同步驱动
只是为了迁移，没有必要。
"""
import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings  # noqa: E402
from app.domain import Base  # noqa: E402  导入即注册全部新表

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 迁移期间 legacy 表仍在库里服务旧接口，autogenerate 不应该提议删掉它们。
LEGACY_TABLES = {
    "accounts",
    "qq_posts",
    "qq_comments",
    "xhs_notes",
    "xhs_comments",
    "ai_configs",
    "ai_tasks",
    "task_logs",
    "chat_sessions",
    "chat_messages",
    "system_configs",
    "post_summaries",
}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in LEGACY_TABLES:
        return False
    if type_ == "table" and reflected and name not in target_metadata.tables:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url_sync,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
