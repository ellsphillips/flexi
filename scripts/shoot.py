"""Drive the real application headlessly and export SVG screenshots.

Snapshots (``tests/snapshot/``) are for regression; this is for "show me what it
looks like". Same seed either way, so a reviewer and a failing test are looking
at the same six weeks.

    uv run python scripts/shoot.py
    rsvg-convert -w 1600 docs/shots/dashboard-wide.svg -o /tmp/wide.png
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import time_machine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from flexi.app import FlexiApp  # noqa: E402
from flexi.models.database.app import create_db_engine, get_session  # noqa: E402
from flexi.models.database.db import Base  # noqa: E402
from flexi.services.samples import NOW, seed_demo  # noqa: E402

SHOTS = ROOT / "docs" / "shots"

WIDE = (120, 36)
NARROW = (84, 28)
TINY = (64, 22)

SHOOTS: tuple[tuple[str, tuple[int, int], list[str]], ...] = (
    ("dashboard-wide", WIDE, []),
    ("dashboard-month", WIDE, ["m"]),
    ("dashboard-day", WIDE, ["d"]),
    ("records-expanded", WIDE, ["v", "r", "down", "down", "space"]),
    ("jump-mode", WIDE, ["v"]),
    ("help", WIDE, ["question_mark"]),
    ("absence-modal", WIDE, ["A"]),
    ("insights", WIDE, ["f2"]),
    ("insights-tall", (120, 44), ["f2"]),
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
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        await pilot.pause()
        app.save_screenshot(str(SHOTS / f"{name}.svg"))
        # A plain-text twin. An SVG has to be rendered before it can be read,
        # and a font without box-drawing coverage turns every strip into a row
        # of tofu — which looks like a Flexi bug and is not one. The text dump
        # is what alignment is actually checked against.
        (SHOTS / f"{name}.txt").write_text(screen_text(app), encoding="utf-8")
    print(f"  {name}.svg  {size[0]}x{size[1]}")  # noqa: T201


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
    build_database(db).close()

    with time_machine.travel(NOW, tick=False):
        for name, size, keys in SHOOTS:
            await shoot(name, size, keys, db)
    db.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
