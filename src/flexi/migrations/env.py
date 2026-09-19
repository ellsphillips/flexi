from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.util.exc import CommandError
from sqlalchemy import URL
from sqlalchemy.engine import Engine

from flexi.models.database.db import Base
from flexi.models.database.engine import create_db_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def requested_database() -> Path:
    """Return the database named by `-x db=` on the command line.

    `alembic.ini` carries no URL, so a command-line run has no default and must
    name the file. The application injects its own engine instead.
    """
    given = context.get_x_argument(as_dictionary=True).get("db")
    if not given:
        message = (
            "No database given. Flexi migrates its own when it starts; to run"
            " a migration by hand, name the file:"
            " alembic -x db=/tmp/scratch.db upgrade head"
        )
        raise CommandError(message)
    return Path(given)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live connection)."""
    url = URL.create("sqlite", database=str(requested_database()))
    context.configure(
        url=url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_column_names=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live database connection.

    `flexi.models.database.migrate` injects its engine, pragma and all, through
    `config.attributes`: a path cannot round-trip through a config value, since
    ConfigParser reads `%` as an interpolation and SQLAlchemy's URL parser reads
    `?` as a query string. A command-line run passes no engine and uses `-x db=`.
    """
    provided_engine = config.attributes.get("engine")
    if provided_engine is not None:
        if not isinstance(provided_engine, Engine):
            message = "Alembic's injected 'engine' attribute must be an Engine"
            raise TypeError(message)
        with provided_engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        return

    connectable = create_db_engine(requested_database())
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
