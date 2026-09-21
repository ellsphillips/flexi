"""Fixtures every test gets, and the two seams set before the imports.

`flexi.config` resolves `CONFIG` at import and every `BINDINGS` list reads it at
class-definition time, both during collection and before any fixture runs, so a
real `~/.config/flexi/config.yaml` would decide what the suite asserts. The
autouse `_never_the_real_home` below is function-scoped and runs too late to
close that. Ruff exempts `os.environ` mutation between imports from `E402`.
"""

import atexit
import os
import shutil
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="flexi-config-")
# Rich and Textual read these at construction time. Colour and cursor assertions
# state a full-colour terminal; the monochrome tests set NO_COLOR themselves.
os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"

import asyncio
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from hypothesis import settings
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from textual.message_pump import MessagePump
from textual.pilot import Pilot

from flexi import wallclock
from flexi.models.database.db import WorkSession
from flexi.models.database.engine import create_db_engine, get_session
from flexi.services import setup
from tests.database import create_schema

settings.register_profile("dev", max_examples=100, deadline=None)
"""How hard Hypothesis tries by default.

No deadline: under `-n auto` a worker can be descheduled mid-example, and a
per-example time limit turns a loaded machine into a failing test. Every health
check stays on; a test that needs one suppressed says so itself.
"""

settings.register_profile("ci", parent=settings.get_profile("dev"), max_examples=500)
"""Five times the examples, for an unattended run."""

settings.register_profile(
    "thorough", parent=settings.get_profile("dev"), max_examples=5000
)
"""For hunting a suspected property failure: `-p no:randomly
--hypothesis-profile=thorough`."""

PROFILES = ("dev", "ci", "thorough")
"""Checked against: `load_profile` accepts an unregistered name and silently
leaves the current profile in place."""

_WANTED = os.environ.get("HYPOTHESIS_PROFILE", "dev")
if _WANTED not in PROFILES:
    _MSG = f"HYPOTHESIS_PROFILE={_WANTED!r} is not one of {PROFILES}"
    raise RuntimeError(_MSG)
settings.load_profile(_WANTED)


# Below the imports, where `E402` allows a call. Every xdist worker imports this
# file, so every process removes the directory it made.
atexit.register(shutil.rmtree, os.environ["XDG_CONFIG_HOME"], ignore_errors=True)

# So a failed `__all__` check names the module, not `assert False`.
pytest.register_assert_rewrite("tests.public_api")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test.")


LATE_CALLBACKS = "FLEXI_LATE_CALLBACKS"
"""Seconds to hold every deferred callback behind a timer. Off unless exported.

    FLEXI_LATE_CALLBACKS=0.02 uv run pytest

`Pilot.pause` drains the messages queued at the moment it is called, so whether
a callback a layout scheduled has landed by then depends on how far the app got
first. A timer is the one thing `pause` cannot drain, so this reproduces a
loaded machine's ordering on an idle one.
"""

SETTLE_PASSES = 20
"""How many pumps `settled` gives the deferred work before giving up on it."""


class Deferred:
    """Work `call_after_refresh` has scheduled and not yet run."""

    outstanding: int = 0
    delay: float = 0.0


DEFERRED = Deferred()
"""Shared with :func:`settled`, and reset for every test."""


@pytest.fixture(autouse=True)
def _count_deferred_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a tally of the callbacks a layout has scheduled and not yet run.

    Counting only: the callbacks still run when they would have. :func:`settled`
    does the waiting, where a test asks for it.
    """
    DEFERRED.outstanding = 0
    DEFERRED.delay = float(os.environ.get(LATE_CALLBACKS) or 0)
    schedule = MessagePump.call_after_refresh

    def counted(
        this: MessagePump, callback: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> bool:
        async def run() -> None:
            # Awaited, not just called: `Widget.recompose` is a coroutine
            # function, and dropping the coroutine leaves the key strip
            # uncomposed.
            try:
                result = callback(*args, **kwargs)
                if inspect.isawaitable(result):
                    await result
            finally:
                DEFERRED.outstanding -= 1

        held = DEFERRED.delay
        scheduled = schedule(this, (lambda: this.set_timer(held, run)) if held else run)
        if scheduled:
            DEFERRED.outstanding += 1
        return scheduled

    monkeypatch.setattr(MessagePump, "call_after_refresh", counted)


async def settled(pilot: Pilot[Any]) -> None:
    """Pump until the work a first layout deferred has actually run.

    `RecordsModule` cannot measure its strip column until the table under it has
    been laid out, so it defers the measurement, and the re-measure rebuilds the
    table from the ledger. Landing late, it overwrites what the test set up: an
    emptied table fills again, an invalidated ledger cache refills.
    `pilot.pause()` drains the messages queued at the moment it is called, so
    what has landed by then is a property of the machine. This waits for the
    callbacks themselves, so a test begins with nothing in flight.
    """
    for _ in range(SETTLE_PASSES):
        if not DEFERRED.outstanding:
            return
        await pilot.pause()
        if DEFERRED.outstanding:
            # Only a real wait moves a timer on, and under FLEXI_LATE_CALLBACKS
            # the callbacks sit behind one.
            await asyncio.sleep(DEFERRED.delay)


@pytest.fixture(autouse=True)
def _never_the_real_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test may reach the database the developer actually uses.

    Every module asks :func:`flexi.locations.database_file` where the database
    is, and that function reads the environment on each call, so one variable
    redirects all of them, including a module added later. Patching the binding
    module by module misses whichever module the fixture has not heard of.
    """
    home = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    setup.clear_initialisation_cache()


@pytest.fixture(autouse=True)
def _never_the_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach the network.

    Startup fills an empty bank holiday cache and `FlexiApp.on_mount` starts a
    worker that asks PyPI for a newer Flexi. Both go through `httpx.Client`, so
    one seam covers both. `fetch_and_cache` treats a connection error as "no
    calendar", so refusing the connection exercises the path a first run offline
    takes.
    """

    def refused(*_args: object, **_kwargs: object) -> None:
        msg = "the test suite does not make network requests"
        raise httpx.ConnectError(msg)

    monkeypatch.setattr(httpx.Client, "send", refused)


@pytest.fixture(scope="session", autouse=True)
def _one_timezone_everywhere() -> Iterator[None]:
    """Take every reading in one zone, whatever the machine is set to.

    Flexi records local wall time, and time_machine reads a naive target as
    UTC, which puts the frozen clock an hour later on a BST laptop than on a
    UTC runner. Pinning :mod:`flexi.wallclock` works everywhere, where ``TZ``
    and :func:`time.tzset` are POSIX only, and it leaves the machine's own zone
    alone, so passing under both timezone matrix rows is evidence that no
    reading escapes the seam.

    Session scope because the snapshot demo database is seeded once per module,
    outside any test, where a function-scoped pin is not in force.
    """
    with wallclock.pinned(UTC):
        yield


@pytest.fixture
def in_london() -> Iterator[None]:
    """Run a test on a British clock.

    The suite is pinned to UTC, a zone with no transitions. Nested inside that
    pin, which is autouse and already in force.
    """
    with wallclock.pinned(ZoneInfo("Europe/London")):
        yield


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """An empty database on disk with every table created, disposed on the way out."""
    created = create_db_engine(tmp_path / "test.db")
    try:
        create_schema(created)
        yield created
    finally:
        created.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with get_session(engine) as open_session:
        yield open_session


@contextmanager
def session_at(path: Path) -> Iterator[Session]:
    """A session on a database a test has already built, engine and all.

    For the databases no fixture owns. `get_session` takes an engine its caller
    disposes of, so `get_session(create_db_engine(path))` leaves that engine's
    connection open after the session closes.
    """
    opened = create_db_engine(path)
    try:
        with get_session(opened) as open_session:
            yield open_session
    finally:
        opened.dispose()


def sessions_on(session: Session, when: date) -> list[WorkSession]:
    """Every session that counts on a date, straight from the table."""
    stmt = select(WorkSession).where(
        WorkSession.work_date == when, WorkSession.voided.is_(False)
    )
    return list(session.execute(stmt).scalars())
