from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test.")


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """An empty database on disk, with every table created."""
    created = create_db_engine(tmp_path / "test.db")
    Base.metadata.create_all(created)
    return created


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with get_session(engine) as open_session:
        yield open_session
