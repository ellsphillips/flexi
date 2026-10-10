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
from sqlalchemy import Engine, event, select
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session
from sqlalchemy.pool import ConnectionPoolEntry
from textual.app import App
from textual.message_pump import MessagePump
from textual.pilot import Pilot

from flexi import wallclock
from flexi.models.database.db import WorkSession
from flexi.models.database.engine import create_db_engine, get_session
from flexi.services import setup
from tests.database import create_schema

settings.register_profile("dev", max_examples=100, deadline=None, derandomize=True)
"""How hard Hypothesis tries by default.

No deadline: under `-n auto` a worker can be descheduled mid-example, and a
per-example time limit turns a loaded machine into a failing test. Every health
check stays on; a test that needs one suppressed says so itself.

Derandomized: each property draws from a seed its own source decides, so a run
tries the examples the last one did, and a failure in CI fails again here. That
also leaves out the example database, which would replay one machine's past
failures on it and nowhere else.
"""

settings.register_profile("ci", parent=settings.get_profile("dev"), max_examples=500)
"""Five times the examples, for an unattended run."""

settings.register_profile(
    "thorough",
    parent=settings.get_profile("dev"),
    max_examples=5000,
    derandomize=False,
)
"""For hunting a suspected property failure: `HYPOTHESIS_PROFILE=thorough`.

Random, unlike the other two, so each hunt searches somewhere new. A failure it
finds prints its falsifying example, to be pinned with `@example`."""

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


@event.listens_for(Engine, "connect")
def _commit_without_waiting_on_the_disk(
    dbapi_connection: DBAPIConnection, _connection_record: ConnectionPoolEntry
) -> None:
    """Let every database the suite opens commit to memory, not to the disk.

    A test database is thrown away, so it needs neither the flush behind each
    commit nor a journal file created and deleted around it. On a hosted
    runner's system drive those cost 20 to 36 ms a commit, against 0.06 ms
    without them, and a Windows worker committing a month of punches outran
    `--timeout`. A rollback journal held in memory is still a rollback journal:
    locking and rollback behave as they do in Flexi. Registered on the class, so
    the engines the app and Alembic make for themselves are covered too.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA synchronous = OFF")
    cursor.execute("PRAGMA journal_mode = MEMORY")
    cursor.close()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test.")


LATE_CALLBACKS = "FLEXI_LATE_CALLBACKS"
"""Seconds to hold every deferred callback behind a timer. Off unless exported.

    FLEXI_LATE_CALLBACKS=0.02 uv run pytest

`Pilot.pause` waits for posted messages, not for a callback a layout deferred,
so whether one has landed by then depends on how far the app got first. Behind
a timer it lands as late as a loaded machine's would, so this reproduces that
ordering on an idle one.
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


REDELIVERY_WAIT = 1 / 50
"""How long each further pause waits, as a fixed time and not a guess at idleness.

Long enough for the timer the app holds a terminal resize behind, 1/120 s.
"""


def _undelivered(app: App[Any]) -> bool:
    """Whether a message posted to the app or a widget on its screens waits unread.

    A layout still owed counts too: `Pilot.pause` performs it on the way out, and
    the `Resize` events it posts are what a resized widget answers. So does a
    terminal resize the app is still holding back before it tells the screens.
    """
    nodes: list[MessagePump] = [app]
    for screen in app.screen_stack:
        nodes.extend(screen.walk_children(with_self=True))
    return (
        app._resize_event is not None
        or app.screen._layout_required
        or any(
            not node._message_queue.empty() or node._next_callbacks for node in nodes
        )
    )


@pytest.fixture(autouse=True)
def _pause_until_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `pilot.pause()` return once every message posted so far has been read.

    Textual's pause waits until the process has used no CPU for a moment, then
    lays the screen out. A process descheduled on a loaded runner uses no CPU
    either, so the pause can end before a bubbled `Input.Changed` reaches the
    screen, and the layout it ends on posts `Resize` events nothing has read yet.
    One pause is enough on a laptop and not on a three-core runner. This pauses
    again while anything is undelivered, so a test reads a screen that has
    answered everything it was sent. `settled` still waits for deferred callbacks,
    which a pause cannot see.
    """
    pause = Pilot.pause

    async def delivered(this: Pilot[Any], delay: float | None = None) -> None:
        await pause(this, delay)
        for _ in range(SETTLE_PASSES):
            if not _undelivered(this.app):
                return
            await pause(this, REDELIVERY_WAIT)

    monkeypatch.setattr(Pilot, "pause", delivered)


async def settled(pilot: Pilot[Any]) -> None:
    """Pump until the work a first layout deferred has actually run.

    `RecordsModule` cannot measure its strip column until the table under it has
    been laid out, so it defers the measurement, and the re-measure rebuilds the
    table from the ledger. Landing late, it overwrites what the test set up: an
    emptied table fills again, an invalidated ledger cache refills.
    `pilot.pause()` waits for posted messages and not for deferred callbacks, so
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
