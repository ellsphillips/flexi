"""Drive the real application headlessly and export SVG screenshots.

Uses the same demo seed and frozen clock as the regression snapshots in
``tests/snapshot/``.

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

# Set before `flexi.config` is imported: CONFIG resolves at import and every
# BINDINGS list reads it at class-definition time, so a local hotkey or opening
# period would be baked into the committed shots.
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="flexi-config-")

from flexi import wallclock  # noqa: E402
from flexi.app import FlexiApp  # noqa: E402
from flexi.models.database.db import Base  # noqa: E402
from flexi.models.database.engine import create_db_engine, get_session  # noqa: E402
from flexi.services.samples import NOW, TIMEZONE, seed_demo  # noqa: E402

# Pinned to the demo timezone, through the same seam the snapshot suite uses.
# Unpinned, a capture carries the local offset.
PINNED = wallclock.pinned(ZoneInfo(TIMEZONE))


def refuse_the_network() -> None:
    """Block outbound HTTP so a shot never depends on the network.

    At mount the application fills an empty bank holiday cache from GOV.UK and
    asks PyPI for a newer version, both in worker threads.
    """

    def refused(*_args: object, **_kwargs: object) -> None:
        msg = "the shots do not make network requests"
        raise httpx.ConnectError(msg)

    httpx.Client.get = refused  # type: ignore[method-assign]


refuse_the_network()

SHOTS = ROOT / "docs" / "shots"

WIDE = (120, 36)
NARROW = (84, 28)
TINY = (63, 22)  # one column under TINY_COLUMNS, so the -tiny rules apply

# The shots the README points at, sized to hold a whole feature.
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
    # Set per instance: textual reads TEXTUAL_ANIMATIONS at import time. A
    # capture landing mid-tween cannot be reproduced.
    app.animation_level = "none"
    async with app.run_test(size=size) as pilot:
        await settled(pilot, app)
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        await settled(pilot, app)
        app.save_screenshot(str(SHOTS / f"{name}.svg"))
        # A plain-text twin: alignment is checked against this, and reading an
        # SVG needs a renderer with box-drawing coverage.
        (SHOTS / f"{name}.txt").write_text(screen_text(app), encoding="utf-8")

    print(f"  {name}.svg  {size[0]}x{size[1]}")


SETTLE_PASSES = 20
"""How many pumps to give a screen before accepting that it has stopped."""


async def settled(pilot: Pilot[None], app: FlexiApp) -> None:
    """Pump until two passes running render the same thing.

    A module that measures itself after its first layout redraws when that
    measurement lands, so a single `pause` can capture an intermediate frame.
    """
    previous = ""
    for _ in range(SETTLE_PASSES):
        await pilot.pause()
        current = screen_text(app)
        if current == previous:
            return
        previous = current


def screen_text(app: FlexiApp) -> str:
    """Return what the compositor would put on the terminal, as characters."""
    strips = app.screen._compositor.render_strips()  # noqa: SLF001
    return "\n".join(
        "".join(segment.text for segment in strip).rstrip() for strip in strips
    )


async def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    db = ROOT / ".demo.db"
    db.unlink(missing_ok=True)

    # Seeded under the frozen clock as well as captured under it; the two have
    # to match.
    try:
        with PINNED, time_machine.travel(NOW, tick=False):
            build_database(db).close()
            for name, size, keys in SHOOTS:
                await shoot(name, size, keys, db)
    finally:
        db.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
