import asyncio
import inspect
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import HealthCheck, settings
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from textual.message_pump import MessagePump
from textual.pilot import Pilot

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.services import samples, setup

settings.register_profile(
    "dev",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
"""How hard Hypothesis tries by default.

No deadline: the suite runs under `-n auto`, so a worker can be descheduled
mid-example and a per-example time limit turns a loaded laptop into a failing
test. Hypothesis's own shrinking still bounds the work, and `--timeout` catches
a genuine hang.

`function_scoped_fixture` is suppressed because the database fixtures here build
one empty SQLite file and are cheap to reuse across examples; the health check
exists to warn that the fixture is not reset per example, and every property
test that takes one either resets it or does not care.
"""

settings.register_profile("ci", parent=settings.get_profile("dev"), max_examples=500)
"""Five times the examples, for the run nobody is waiting on."""

settings.register_profile(
    "thorough", parent=settings.get_profile("dev"), max_examples=5000
)
"""For deliberately hunting a suspected property failure: `-p no:randomly
--hypothesis-profile=thorough`."""

settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test.")

    # Pin the timezone before anything imports, and before time_machine freezes
    # anything. Flexi records local wall time, so "local" has to be a fixed
    # thing or the expectations move with the machine -- and time_machine reads
    # a naive target as UTC, which put the frozen clock an hour later on a BST
    # laptop than on a UTC runner. That is precisely how the committed snapshots
    # came to have an hour of British Summer Time baked into them.
    os.environ["TZ"] = samples.TIMEZONE
    if hasattr(time, "tzset"):  # POSIX only; the suite does not run on Windows
        time.tzset()


LATE_CALLBACKS = "FLEXI_LATE_CALLBACKS"
"""Seconds to hold every deferred callback behind a timer. Off unless exported.

    FLEXI_LATE_CALLBACKS=0.02 uv run pytest

`Pilot.pause` drains the messages queued *at the moment it is called*, so a
callback that a layout schedules may or may not have landed when it returns.
That is a function of how far the app got before `pause` started, which is a
function of how loaded the machine is -- the whole difference between a laptop
and a three-core runner, and not a difference any assertion should turn on.

A timer is the one thing `pause` cannot drain, so this reproduces a loaded
runner's ordering on an idle machine, deterministically. It is how the two
failures this fixture exists to prevent were reproduced locally in under a
second, and it should stay green: a test that passes without it and fails with
it is asserting on a screen that had not finished drawing.
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

    Counting only -- the callbacks still run exactly when they would have. It is
    :func:`settled` that does the waiting, and only where a test asks for it.
    """
    DEFERRED.outstanding = 0
    DEFERRED.delay = float(os.environ.get(LATE_CALLBACKS) or 0)
    schedule = MessagePump.call_after_refresh

    def counted(
        this: MessagePump, callback: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> bool:
        async def run() -> None:
            # Awaited, not just called: `Widget.recompose` is a coroutine
            # function, and a wrapper that drops the coroutine silently stops
            # the key strip from ever being composed.
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
    been laid out, so it defers that measurement -- and the re-measure rebuilds
    the table from the ledger. Land it a moment late and it overwrites what the
    test had just set up: a table the test emptied fills again, a ledger cache
    the test had just invalidated refills. Both are real CI failures, and both
    read as a bug in the thing under test rather than in the waiting.

    `pilot.pause()` cannot express this. It drains the messages queued at the
    moment it is called, so whether a callback scheduled by a layout has landed
    when it returns depends on how far the application got first -- which is a
    property of the machine, not of the code. This waits for the callbacks
    themselves, so a test that begins after it begins with nothing in flight.
    """
    for _ in range(SETTLE_PASSES):
        if not DEFERRED.outstanding:
            return
        await pilot.pause()
        if DEFERRED.outstanding:
            # Only a real wait moves a timer on, and under FLEXI_LATE_CALLBACKS
            # the callbacks are sitting behind one.
            await asyncio.sleep(DEFERRED.delay)


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
