"""Flexi reads files from beside its own __file__, so they have to ship.

A wheel that carries only the .py files installs cleanly, imports cleanly, and
then dies on the first frame with a StylesheetError. These assertions run
against whatever copy of the package is on the path, so running the suite
against an installed wheel checks the built artefact rather than the source.
"""

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

import flexi
from flexi.app import FlexiApp
from flexi.theme import THEME_PATH

PACKAGE = Path(flexi.__file__).parent
PROJECT_ROOT = Path(__file__).parent.parent

# Import name on the left, distribution name on the right, where they differ.
DISTRIBUTION = {"yaml": "pyyaml"}

NEVER_IMPORTED = {"tzdata"}
"""Dependencies that are data rather than code, so no import can find them.

`tzdata` is the zoneinfo database, which Windows does not ship and
:mod:`zoneinfo` finds by looking for the package rather than by importing it.
Declared here so the "declared and unused" check keeps its teeth: the exception
is one name with a reason, not a hole in the rule.
"""

DATA_FILES = [
    "py.typed",
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
    assert "$c-" in THEME_PATH.read_text(encoding="utf-8")


def test_the_package_ships_its_typing_marker() -> None:
    """Without py.typed, a downstream mypy silently ignores every annotation."""
    assert (PACKAGE / "py.typed").is_file()


@pytest.mark.skipif(not PROJECT_ROOT.joinpath("README.md").is_file(), reason="sdist")
def test_the_readme_version_badge_matches_the_project() -> None:
    """A hand-written badge is a fact that drifts the first time nobody looks."""
    spec = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    badges = re.findall(r"/badge/version-([\d.]+)-", readme)
    assert badges, "the README no longer carries a version badge"
    assert set(badges) == {spec["project"]["version"]}


def _declared() -> set[str]:
    spec = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        re.split(r"[<>=\[]", raw)[0].strip().lower().replace("-", "_")
        for raw in spec["project"]["dependencies"]
    }


def _imported() -> set[str]:
    found: set[str] = set()
    for path in Path(flexi.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return {
        DISTRIBUTION.get(name, name)
        for name in found - sys.stdlib_module_names - {"flexi"}
    }


@pytest.mark.skipif(
    not PROJECT_ROOT.joinpath("pyproject.toml").is_file(), reason="sdist"
)
def test_every_import_is_a_declared_dependency() -> None:
    """A transitive import works until the day the resolver drops it."""
    assert _imported() - _declared() == set()


@pytest.mark.skipif(
    not PROJECT_ROOT.joinpath("pyproject.toml").is_file(), reason="sdist"
)
def test_no_dependency_is_declared_and_unused() -> None:
    assert _declared() - _imported() - NEVER_IMPORTED == set()
