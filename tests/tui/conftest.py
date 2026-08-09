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

import pytest
import time_machine

from flexi.app import FlexiApp
from flexi.components.chrome import AppFooter
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.screens.dashboard import DashboardScreen
from flexi.services.samples import NOW, seed_demo

WIDE = (120, 36)


@pytest.fixture
def _frozen() -> Iterator[None]:
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
def app_factory(seeded_db: Path) -> Callable[[], FlexiApp]:
    def build() -> FlexiApp:
        return FlexiApp(db_path=seeded_db)

    return build


def dashboard(app: FlexiApp) -> DashboardScreen:
    """The dashboard, wherever it is on the stack."""
    found = app._dashboard()
    assert found is not None, "the dashboard should be mounted"
    return found


def status_text(app: FlexiApp) -> str:
    """Whatever the status bar is currently saying."""
    footer = app.screen.query_one(AppFooter)
    message = footer.query_one("#status-message")
    return str(message.render())


def screen_text(app: FlexiApp) -> str:
    """The rendered characters, for assertions about what is actually drawn."""
    strips = app.screen._compositor.render_strips()
    return "\n".join("".join(segment.text for segment in strip) for strip in strips)
