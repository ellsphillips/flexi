"""Visual regression, against text rather than against SVG.

``pytest-textual-snapshot`` compares rendered SVGs, and an SVG diff is only
readable as a picture — which means a CI failure is a file you have to download
before you can tell whether the change was intended.

So the snapshots here are the *characters* the compositor produced, committed
alongside the SVGs in ``docs/shots/``. A failure prints a unified diff of two
screens, in the terminal, where the person who caused it is already looking. The
SVGs are still written by ``scripts/shoot.py`` and are still what a reviewer
looks at; they are just not what the test asserts on.

Regenerate deliberately:

    uv run python scripts/shoot.py

and read the diff before committing it.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest
import time_machine

from flexi.app import FlexiApp
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.services.samples import NOW, seed_demo
from tests.tui.conftest import screen_text

SHOTS = Path(__file__).resolve().parent.parent.parent / "docs" / "shots"

WIDE = (120, 36)
NARROW = (84, 28)
TINY = (64, 22)

CASES: tuple[tuple[str, tuple[int, int], list[str]], ...] = (
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


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("snapshot") / "flexi.db"
    with time_machine.travel(NOW, tick=False):
        engine = create_db_engine(path)
        Base.metadata.create_all(engine)
        session = get_session(engine)
        seed_demo(session)
        session.close()
        engine.dispose()
    return path


@pytest.mark.parametrize(
    ("name", "size", "keys"), CASES, ids=[case[0] for case in CASES]
)
async def test_screen_matches_its_committed_render(
    name: str, size: tuple[int, int], keys: list[str], demo_db: Path
) -> None:
    """It draws what it drew last time, or says exactly what changed."""
    expected_path = SHOTS / f"{name}.txt"
    assert expected_path.exists(), (
        f"{expected_path} is missing — run `uv run python scripts/shoot.py`"
    )

    with time_machine.travel(NOW, tick=False):
        app = FlexiApp(db_path=demo_db)
        # Matches scripts/shoot.py. See the note there: an animating widget
        # renders whatever frame the capture happens to land on.
        app.animation_level = "none"
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            await pilot.pause()
            actual = "\n".join(line.rstrip() for line in screen_text(app).splitlines())

    expected = expected_path.read_text(encoding="utf-8").rstrip("\n")
    if actual.rstrip("\n") == expected:
        return

    diff = "\n".join(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=f"{name}.txt (committed)",
            tofile=f"{name} (now)",
            lineterm="",
        )
    )
    pytest.fail(
        f"{name} at {size[0]}x{size[1]} no longer renders as committed.\n"
        f"If the change was intended, run `uv run python scripts/shoot.py`.\n\n{diff}"
    )
