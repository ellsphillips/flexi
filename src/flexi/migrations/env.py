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
    """The database named on the command line, for a run Flexi did not start.

    `alembic.ini` names none. The application hands its own engine over below,
    and a URL in the file is a second answer to where the database is. The
    answer it gave was `flexi.db` in the working directory, so `alembic upgrade
    head` in a checkout migrated a database into the checkout.
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

    `flexi.models.database.migrate` hands its own engine over in
    `config.attributes`, already carrying the pragma, because a path is not
    safe to round-trip through a config value: ConfigParser reads `%` as an
    interpolation and SQLAlchemy's URL parser reads `?` as a query string.
    Running `alembic` from the command line passes no engine and names its
    database with `-x db=`.
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
