import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.services import setup


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


@pytest.fixture(autouse=True)
def _never_the_real_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test may reach the database the developer actually uses.

    Every module asks :func:`flexi.locations.database_file` where the database
    is, and that function reads the environment each time it is called -- so one
    variable redirects all of them, including a module added next year. Fixtures
    that patched the binding module by module missed whichever module they had
    not heard of: `tests/test_balance_cli.py` covered three and not
    `flexi.services.setup`, so `requires_setup` consulted the real machine.

    The consequence was a suite that passed or failed on whether the person
    running it had ever set Flexi up. It went green for months and then turned
    red the first time a reset removed the developer's own records -- which is a
    worse failure than a red suite, because for all that time it was reporting
    on a database no test had written.
    """
    home = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    setup._INITIALISED.clear()


@pytest.fixture(autouse=True)
def _never_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may ask PyPI whether there is a newer Flexi.

    `FlexiApp.on_mount` starts a worker that does, and Textual waits for its
    workers as the application exits, so every one of the hundred-odd interface
    tests paid for a name lookup. On a machine that resolves quickly that is
    invisible. On one that does not it took the suite from two minutes to nine,
    which makes the timing of CI a function of the weather rather than of the
    code, and makes a slow afternoon look like somebody's regression.

    Patched where it is bound, not where it is defined: `flexi.app` imports the
    function by name at module scope, so rebinding it in `flexi.versioning`
    would leave the application holding the original.
    """
    monkeypatch.setattr("flexi.app.available_update", lambda: None)
    monkeypatch.setattr("flexi.versioning.available_update", lambda: None)


@pytest.fixture(autouse=True)
def _never_the_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach GOV.UK.

    Startup now fills an empty bank holiday cache, which every test that opens
    a database goes through. A suite that quietly makes real requests is slow,
    fails on a train, and passes for the wrong reason when the fetch happens to
    succeed. `fetch_and_cache` already treats a connection error as "no
    calendar", so refusing the connection exercises the path a first run
    offline actually takes.
    """

    def refused(*_args: object, **_kwargs: object) -> None:
        msg = "the test suite does not make network requests"
        raise httpx.ConnectError(msg)

    monkeypatch.setattr(httpx.Client, "get", refused)


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
