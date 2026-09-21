"""Build isolated test databases without committing every schema statement."""

from sqlalchemy import Engine

from flexi.models.database.db import Base


def create_schema(engine: Engine) -> None:
    """Create the model schema, including its triggers, in one SQLite transaction.

    SQLite's legacy transaction mode does not begin a transaction for DDL, even
    inside SQLAlchemy's transaction context. Starting one explicitly avoids a
    disk flush for every table and index without changing connection settings
    or any transaction performed by the test itself.
    """
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN")
        Base.metadata.create_all(connection)
