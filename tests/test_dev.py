"""The development entry point, which nothing else imports.

``src/flexi/dev.py`` is run by path -- ``textual run --dev ./src/flexi/dev.py``
-- so it is the one module in Flexi that no other module will notice has stopped
working. It names two things it does not own, and a rename on either side breaks
it silently: the first anybody hears is that the developer tools do not start,
at the moment somebody reached for them to debug something else.

So it is loaded here the way ``textual run`` loads it, by path, rather than
imported. Both of the things it calls are replaced first: a real
``run_migrations`` would touch the developer's own database, and a real
``FlexiApp().run()`` would take the terminal the suite is running in.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

import flexi
import flexi.app
import flexi.models.database.migrate

DEV_SCRIPT = Path(flexi.__file__).with_name("dev.py")
"""Named by path, never imported -- the same way the runner is reached for."""


@pytest.fixture
def watched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand-ins for the migration and the application, in the order called.

    Patched on the modules the runner imports *from*, so a runner that reached
    for a different migration entry point, or built some other application,
    would get the real one and be caught doing it.
    """
    happened: list[str] = []

    class Fake:
        def __init__(self) -> None:
            happened.append("built")

        def run(self, *_args: Any, **_kwargs: Any) -> None:
            happened.append("ran")

    monkeypatch.setattr(
        flexi.models.database.migrate,
        "run_migrations",
        lambda: happened.append("migrated"),
    )
    monkeypatch.setattr(flexi.app, "FlexiApp", Fake)
    return happened


def test_loading_the_dev_runner_by_any_other_name_starts_nothing(
    watched: list[str],
) -> None:
    """Everything it does sits behind the `__main__` guard.

    Without the guard, merely importing this module -- which anything walking
    the package does -- migrates a database and takes the terminal for a
    Textual application that nobody asked for.
    """
    runpy.run_path(str(DEV_SCRIPT))

    assert watched == []


def test_running_the_dev_script_migrates_before_it_opens_the_application(
    watched: list[str],
) -> None:
    """A developer's database is usually a schema behind the branch.

    Migrating after the application is up is a SQLAlchemy error on a table that
    has been renamed, several screens in -- and by then the traceback is about
    the screen rather than about the schema.
    """
    runpy.run_path(str(DEV_SCRIPT), run_name="__main__")

    assert watched == ["migrated", "built", "ran"]
