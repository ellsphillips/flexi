from __future__ import annotations

from logging.config import fileConfig
from typing import cast

from alembic import context
from sqlalchemy import engine_from_config, event, pool
from sqlalchemy.engine import Engine

from flexi.models.database.db import Base
from flexi.models.database.engine import enforce_foreign_keys

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_column_names=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live database connection.

    `flexi.models.database.migrate` hands its own engine over in
    `config.attributes`, already carrying the pragma, because a path is not
    safe to round-trip through a config value: ConfigParser reads `%` as an
    interpolation and SQLAlchemy's URL parser reads `?` as a query string.
    Running `alembic` from the command line passes no engine and still reads
    `alembic.ini` as it always did.
    """
    provided_engine = cast("Engine | None", config.attributes.get("engine"))
    if provided_engine is not None:
        with provided_engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    event.listen(connectable, "connect", enforce_foreign_keys)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
