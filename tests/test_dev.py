"""``scripts/dev.py``, loaded by path the way ``textual run --dev`` loads it.

The migration and the application are both replaced first: the real ones would
touch the developer's own database and take the terminal the suite runs in.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

import flexi.app
import flexi.models.database.migrate

DEV_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev.py"
"""Resolved from this file: an installed package has no `scripts` beside it."""

pytestmark = pytest.mark.skipif(not DEV_SCRIPT.is_file(), reason="sdist")


@pytest.fixture
def watched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand-ins for the migration and the application, in the order called.

    Patched on the modules the runner imports from, so a runner reaching for a
    different entry point gets the real one and is caught doing it.
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


def test_importing_the_dev_script_starts_nothing(
    watched: list[str],
) -> None:
    """Anything that walks the package imports it, so the guard has to hold."""
    runpy.run_path(str(DEV_SCRIPT))

    assert watched == []


def test_dev_script_migrates_before_it_launches(
    watched: list[str],
) -> None:
    runpy.run_path(str(DEV_SCRIPT), run_name="__main__")

    assert watched == ["migrated", "built", "ran"]
