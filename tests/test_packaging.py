"""Flexi reads files from beside its own __file__, so they have to ship.

A wheel that carries only the .py files installs cleanly, imports cleanly, and
then dies on the first frame with a StylesheetError. These assertions run
against whatever copy of the package is on the path, so running the suite
against an installed wheel checks the built artefact rather than the source.
"""

from pathlib import Path

import pytest

import flexi
from flexi.app import FlexiApp
from flexi.theme import THEME_PATH

PACKAGE = Path(flexi.__file__).parent

DATA_FILES = [
    "py.typed",
    "static/welcome.md",
    "migrations/script.py.mako",
    "migrations/env.py",
]


@pytest.mark.parametrize("stylesheet", FlexiApp.CSS_PATH)
def test_every_declared_stylesheet_is_installed(stylesheet: str) -> None:
    assert (PACKAGE / stylesheet).is_file()


@pytest.mark.parametrize("relative", DATA_FILES)
def test_data_files_travel_with_the_package(relative: str) -> None:
    assert (PACKAGE / relative).is_file()


def test_the_theme_can_be_parsed_from_the_installed_stylesheet() -> None:
    """The palette is read out of the .tcss at import, not hard-coded."""
    assert THEME_PATH.is_file()
    assert "$c-" in THEME_PATH.read_text()


def test_the_package_ships_its_typing_marker() -> None:
    """Without py.typed, a downstream mypy silently ignores every annotation."""
    assert (PACKAGE / "py.typed").is_file()
