"""One app, one seeded database, one frozen clock.

Every test in this directory drives the real application through Textual's
``Pilot``. Time is frozen at :data:`flexi.services.samples.NOW` — a Thursday
afternoon with a session open — because half of what the dashboard shows is a
function of *now*, and a test whose expectations drift at midnight is worse than
no test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import time_machine
from textual.screen import Screen

from flexi.app import FlexiApp
from flexi.components.chrome import AppFooter
from flexi.models.database.db import Base
from flexi.models.database.engine import create_db_engine, get_session
from flexi.screens.dashboard import DashboardScreen
from flexi.services.samples import NOW, seed_demo

WIDE = (120, 36)

type AppFactory = Callable[[], FlexiApp]


@pytest.fixture(autouse=True)
def _frozen() -> Iterator[None]:
    """Autouse, because the docstring above has always claimed it was.

    It reached tests only as a dependency of `seeded_db`, so the twenty Pilot
    tests in `test_first_run.py` — which build their own database — ran on the
    real system clock, and the `usefixtures("_frozen")` marks in the files that
    do take `seeded_db` were doing nothing at all.
    """
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def seeded_db(tmp_path: Path, _frozen: None) -> Path:
    """A database holding the demo's six weeks of a working life."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    seed_demo(session)
    session.close()
    engine.dispose()
    return path


@pytest.fixture
def app_factory(seeded_db: Path) -> AppFactory:
    def build() -> FlexiApp:
        return FlexiApp(db_path=seeded_db)

    return build


def dashboard(app: FlexiApp) -> DashboardScreen:
    """The dashboard, wherever it is on the stack."""
    found = app.dashboard()
    assert found is not None, "the dashboard should be mounted"
    return found


def showing[S: Screen[Any]](app: FlexiApp, kind: type[S]) -> S:
    """The current screen, asserted to be ``kind``.

    ``App.screen`` is typed ``Screen[object]``, so narrowing it in place with
    ``isinstance`` against a ``Screen[None]`` subclass leaves mypy holding
    ``Never``. Going through a bound type variable keeps the type, and still
    fails the test when the screen is not the one expected.
    """
    screen = app.screen
    assert isinstance(screen, kind), (
        f"expected {kind.__name__}, showing {type(screen).__name__}"
    )
    return screen


def status_text(app: FlexiApp) -> str:
    """Whatever the status bar is currently saying."""
    footer = app.screen.query_one(AppFooter)
    message = footer.query_one("#status-message")
    return str(message.render())


def screen_text(app: FlexiApp) -> str:
    """The rendered characters, for assertions about what is actually drawn."""
    strips = app.screen._compositor.render_strips()
    return "\n".join("".join(segment.text for segment in strip) for strip in strips)


# -- legibility --------------------------------------------------------------

READABLE = 3.0
"""Contrast a piece of chrome has to clear against the ground behind it.

Below three to one a dim tone stops being text and becomes a texture. Two have
gone out this way: `$c-line` on a tinted calendar cell at 1.02:1, and the same
colour on the header ground at 1.37:1.
"""


def channel(value: int) -> float:
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: tuple[int, int, int]) -> float:
    red, green, blue = (channel(part) for part in colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: tuple[int, int, int], ground: tuple[int, int, int]) -> float:
    """The WCAG ratio between two colours, brighter over darker."""
    pair = sorted((relative_luminance(foreground), relative_luminance(ground)))
    return (pair[1] + 0.05) / (pair[0] + 0.05)
