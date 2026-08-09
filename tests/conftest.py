import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test.")

    # Pin the timezone before anything imports, and before time_machine freezes
    # anything. Flexi records local wall time, so "local" has to be a fixed
    # thing or the expectations move with the machine -- and time_machine reads
    # a naive target as UTC, which put the frozen clock an hour later on a BST
    # laptop than on a UTC runner. That is precisely how the committed snapshots
    # came to have an hour of British Summer Time baked into them.
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):  # POSIX only; the suite does not run on Windows
        time.tzset()


@pytest.fixture
def in_london() -> Iterator[None]:
    """Run a test on a British clock, then put the machine back.

    The suite is pinned to UTC, a zone with no transitions, which is why it
    could not catch a single one of these.
    """
    os.environ["TZ"] = "Europe/London"
    time.tzset()
    try:
        yield
    finally:
        os.environ["TZ"] = "UTC"
        time.tzset()


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
