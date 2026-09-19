"""A database path is a path, not a URL and not a config value.

The database lives under a home directory, so its path holds whatever
punctuation the platform allows. In a ``file:`` URI ``?`` opens a query string
and ``#`` opens a fragment, and ConfigParser reads ``%`` as an interpolation,
so a path pasted into either syntax is truncated or raises. ``#`` and ``%`` are
legal in a filename on all three platforms, and ``%`` is what Windows writes
environment variables with.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from flexi.models.database.db import Settings
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services import setup

AWKWARD = [
    pytest.param("plain", id="the ordinary case, so the test can fail honestly"),
    pytest.param("with space", id="a space, as in a full name"),
    pytest.param("a#b", id="a hash, which opened a URI fragment"),
    pytest.param("100%pure", id="a percent, which opened an interpolation"),
    pytest.param(
        "a?b",
        id="a question mark, which opened a query string",
        marks=pytest.mark.skipif(
            sys.platform == "win32", reason="Windows forbids ? in a filename"
        ),
    ),
]


def _configure(db: Path) -> None:
    """Finish setup, the way the setup screen does."""
    engine = create_db_engine(db)
    with get_session(engine) as session:
        session.add(
            Settings(
                leave_year_start="04-06",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time="18:00",
                contracted_minutes=444,
                day_window_start="07:00",
                day_window_end="19:00",
            )
        )
        session.commit()
    engine.dispose()


@pytest.mark.parametrize("directory", AWKWARD)
def test_awkward_path_migrates_and_reads_back(tmp_path: Path, directory: str) -> None:
    """The whole first run, on a path with punctuation in it.

    Migrating, writing the settings row and being recognised afterwards are
    three separate layers, and `is_initialised` covers the round trip.
    """
    db = tmp_path / directory / "db.db"

    run_migrations(db)
    _configure(db)

    assert setup.is_initialised(db) is True


def test_check_never_creates_the_database(tmp_path: Path) -> None:
    """Asking whether a machine is set up may not set it up.

    The probe opens by path, which carries the punctuation safely on a Windows
    share too. A connection by path creates what it cannot find, so the file is
    looked for first: a zero-byte database stats like an install.
    """
    missing = tmp_path / "a#b" / "db.db"
    missing.parent.mkdir(parents=True)

    assert setup.stamped_and_configured(missing) is False
    assert setup.is_initialised(missing) is False
    assert not missing.exists()
