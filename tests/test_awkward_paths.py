"""A database path is a path, not a URL and not a config value.

Flexi's database lives wherever the machine puts application data, and that is
somebody's home directory with somebody's name in it. Three layers used to
paste that path into a syntax with its own opinion about punctuation:

* ``sqlite3.connect(f"file:{path}?mode=ro")`` -- ``?`` opens the query string
  and ``#`` opens a fragment, so the path was truncated at either and a fully
  configured Flexi reported "not set up on this machine yet", every run.
* ``create_engine(f"sqlite:///{path}")`` -- the same ``?``, so the application
  went looking for a database with half a path.
* Alembic's ``set_main_option`` -- ConfigParser reads ``%`` as an
  interpolation, so a migration raised ``ValueError`` rather than running.

None of the three is exotic. ``#`` and ``%`` are legal in a filename on all
three platforms, and ``%`` is what Windows itself writes environment variables
with.
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
def test_a_database_under_an_awkward_path_migrates_and_reads_back(
    tmp_path: Path, directory: str
) -> None:
    """The whole first run, on a path with punctuation in it.

    Migrating, writing the settings row, and being recognised afterwards are
    three different layers and each one broke differently. Asserting on
    `is_initialised` at the end is what makes this a test of the round trip
    rather than of one of them.
    """
    db = tmp_path / directory / "db.db"

    run_migrations(db)
    _configure(db)

    assert setup.is_initialised(db) is True


def test_the_check_is_read_only_wherever_the_path_leads(tmp_path: Path) -> None:
    """Escaping the path must not lose `mode=ro` along with the punctuation.

    That flag is the invariant `flexi.locations` exists to protect: asking
    whether a machine is set up may not leave a zero-byte database behind on
    one that never was.
    """
    missing = tmp_path / "a#b" / "db.db"
    missing.parent.mkdir(parents=True)

    assert setup.is_initialised(missing) is False
    assert not missing.exists()
