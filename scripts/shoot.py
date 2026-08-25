"""Drive the real application headlessly and export SVG screenshots.

Snapshots (``tests/snapshot/``) are for regression; this is for "show me what it
looks like". Same seed either way, so a reviewer and a failing test are looking
at the same six weeks.

    uv run python scripts/shoot.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import time_machine
from sqlalchemy.orm import Session
from textual.pilot import Pilot

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Before `flexi.config` is imported, because it resolves CONFIG at import and
# every BINDINGS list reads it at class-definition time. For the same reason
# the timezone is pinned below: the command that fixes a failing snapshot
# cannot be the command that causes one, and a developer's own hotkeys or
# opening period would be baked into the committed shots.
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="flexi-config-")

from flexi import wallclock  # noqa: E402
from flexi.app import FlexiApp  # noqa: E402
from flexi.models.database.app import create_db_engine, get_session  # noqa: E402
from flexi.models.database.db import Base  # noqa: E402
from flexi.services.samples import NOW, TIMEZONE, seed_demo  # noqa: E402

# The same pin the snapshot suite applies in `tests/conftest.py`, through the
# same seam. Without it the documented way to regenerate the shots bakes the
# developer's timezone into them -- an hour of British Summer Time that the
# suite, running under UTC, then rejects. The command that fixes a failing
# snapshot cannot be the command that causes one.
#
# It was `TZ` and `time.tzset`, which is POSIX only: on Windows it pinned
# nothing, so the one platform where a contributor could not verify their own
# shots was the one where they came out wrong.
PINNED = wallclock.pinned(ZoneInfo(TIMEZONE))


def refuse_the_network() -> None:
    """No GOV.UK, no PyPI -- the same as the snapshot suite.

    The application fills an empty bank holiday cache at mount and asks PyPI
    for a newer version, both in worker threads. Left alone, the shots came out
    with whatever GOV.UK returned on the day, so April gained a bank holiday
    the demo never seeded and the balance moved by seven hours. A screenshot
    that depends on the machine's internet is not a screenshot of anything.
    """

    def refused(*_args: object, **_kwargs: object) -> None:
        msg = "the shots do not make network requests"
        raise httpx.ConnectError(msg)

    httpx.Client.get = refused  # type: ignore[method-assign]


refuse_the_network()

SHOTS = ROOT / "docs" / "shots"

WIDE = (120, 36)
NARROW = (84, 28)
TINY = (64, 22)

# The shots the README points at. Wider and taller than the regression set, so
# each one has room to show the whole feature rather than a corner of it.
SHOWCASE = (128, 40)
SHOWCASE_TALL = (128, 46)

SHOOTS: tuple[tuple[str, tuple[int, int], list[str]], ...] = (
    ("showcase-dashboard", SHOWCASE, ["m"]),
    ("showcase-records", SHOWCASE, ["v", "r", "down", "down", "space"]),
    ("showcase-leave", SHOWCASE, ["f2", "down", "shift+right", "shift+right"]),
    ("showcase-insights", SHOWCASE_TALL, ["f3"]),
    ("showcase-jump", SHOWCASE, ["v"]),
    ("dashboard-wide", WIDE, []),
    ("dashboard-month", WIDE, ["m"]),
    ("dashboard-day", WIDE, ["d"]),
    ("records-expanded", WIDE, ["v", "r", "down", "down", "space"]),
    ("jump-mode", WIDE, ["v"]),
    ("help", WIDE, ["question_mark"]),
    ("absence-modal", WIDE, ["A"]),
    ("insights", WIDE, ["f3"]),
    ("insights-tall", (120, 44), ["f3"]),
    ("leave", WIDE, ["f2"]),
    ("leave-selection", WIDE, ["f2", "down", "shift+right", "shift+right"]),
    ("leave-narrow", NARROW, ["f2"]),
    ("dashboard-narrow", NARROW, []),
    ("dashboard-tiny", TINY, []),
)


def build_database(path: Path) -> Session:
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    seed_demo(session)
    return session


async def shoot(name: str, size: tuple[int, int], keys: list[str], db: Path) -> None:
    app = FlexiApp(db_path=db)
    # A capture that lands mid-tween is a capture nobody can reproduce, and
    # these are what the snapshot tests compare against. Per-instance, because
    # textual reads TEXTUAL_ANIMATIONS at import time and pytest has already
    # imported it by the time any conftest runs.
    app.animation_level = "none"
    async with app.run_test(size=size) as pilot:
        await settled(pilot, app)
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        await settled(pilot, app)
        app.save_screenshot(str(SHOTS / f"{name}.svg"))
        # A plain-text twin. An SVG has to be rendered before it can be read,
        # and a font without box-drawing coverage turns every strip into a row
        # of tofu — which looks like a Flexi bug and is not one. The text dump
        # is what alignment is actually checked against.
        (SHOTS / f"{name}.txt").write_text(screen_text(app), encoding="utf-8")

    print(f"  {name}.svg  {size[0]}x{size[1]}")


SETTLE_PASSES = 20
"""How many pumps to give a screen before accepting that it has stopped."""


async def settled(pilot: Pilot[None], app: FlexiApp) -> None:
    """Pump until two passes running render the same thing.

    A module that measures itself after its first layout redraws when that
    measurement lands, so a capture taken after a single `pause` is a capture of
    whichever frame the machine happened to have reached. `tests/snapshot/`
    asserts on these files, so a shot taken mid-draw is committed as the truth
    and the suite starts failing on a screen that is drawn correctly.
    """
    previous = ""
    for _ in range(SETTLE_PASSES):
        await pilot.pause()
        current = screen_text(app)
        if current == previous:
            return
        previous = current


def screen_text(app: FlexiApp) -> str:
    """Whatever the compositor would put on the terminal, as characters."""
    strips = app.screen._compositor.render_strips()  # noqa: SLF001
    return "\n".join(
        "".join(segment.text for segment in strip).rstrip() for strip in strips
    )


async def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    db = ROOT / ".demo.db"
    db.unlink(missing_ok=True)

    # Seeded under the frozen clock as well as captured under it, which is what
    # `tests/snapshot/test_screens.py` does. The two have to match.
    with PINNED, time_machine.travel(NOW, tick=False):
        build_database(db).close()
        for name, size, keys in SHOOTS:
            await shoot(name, size, keys, db)
    db.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
