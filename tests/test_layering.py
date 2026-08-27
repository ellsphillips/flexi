"""The layering rule, enforced.

``flexi.domain`` may not import Textual or SQLAlchemy, and ``flexi.components``
may not import SQLAlchemy. Both rules are what keep the arithmetic testable
without a terminal and the widgets testable without a database, and both are the
kind of rule that decays silently the first time someone needs one import "just
here". Twenty lines of AST walking is cheaper than the decay.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "flexi"

FORBIDDEN: dict[str, frozenset[str]] = {
    "domain": frozenset({"textual", "sqlalchemy", "flexi.services", "flexi.models"}),
    "components": frozenset({"sqlalchemy", "flexi.models", "flexi.services.wallet"}),
    "screens": frozenset({"sqlalchemy"}),
    "services": frozenset(
        {"textual", "flexi.app", "flexi.screens", "flexi.components", "flexi.cli"}
    ),
    "cli": frozenset({"flexi.app", "flexi.screens", "flexi.components"}),
    "models": frozenset(
        {
            "textual",
            "httpx",
            "flexi.app",
            "flexi.screens",
            "flexi.components",
            "flexi.services",
            "flexi.cli",
        }
    ),
}
"""Which packages may not reach which.

`models` was the one layer nothing checked, and it is the layer every other one
sits on -- a single upward import there makes the whole graph a cycle. It is
not forbidden `flexi.domain`: nothing reaches for it today, and forbidding it
would pre-judge a move that may turn out to be right.

`services` and `cli` were unconstrained, so nothing stopped a service importing
a widget or the command line importing a screen -- the two directions that would
make the CLI unusable without a terminal.

`components` forbids `sqlalchemy` but permits `flexi.services`, and a widget
imported two value objects from `flexi.services.wallet`, dragging a hundred and
twenty SQLAlchemy modules behind them: the rule satisfied literally and defeated
in substance. Those values live in `flexi.domain.wallet` now, and that one
module is named here so the loophole cannot be reopened by moving them back.
"""


def imported_modules(source: Path) -> Iterator[str]:
    """Every module name the file imports, dotted and absolute."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def module_scope_imports(source: Path) -> Iterator[str]:
    """Only the imports that run when the file is imported.

    `imported_modules` walks the whole tree, which is the right question for a
    layering rule and the wrong one for a startup cost: an import inside a
    function is paid by the command that calls it and by nobody else.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def python_files(package: str) -> list[Path]:
    return sorted((SRC / package).rglob("*.py"))


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_package_exists(package: str) -> None:
    """It fails loudly if a package is renamed and the rule is left behind."""
    assert (SRC / package).is_dir(), f"flexi/{package}/ is missing"


@pytest.mark.parametrize(
    ("package", "path"),
    [
        (package, path)
        for package in sorted(FORBIDDEN)
        if (SRC / package).is_dir()
        for path in python_files(package)
    ],
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_layer_imports(package: str, path: Path) -> None:
    """It keeps each layer inside the imports it is allowed."""
    banned = FORBIDDEN[package]
    for module in imported_modules(path):
        root = module.split(".")[0]
        offending = next(
            (rule for rule in banned if root == rule or module.startswith(f"{rule}.")),
            None,
        )
        assert offending is None, (
            f"{path.relative_to(SRC)} imports {module!r}; "
            f"flexi/{package}/ may not depend on {offending!r}"
        )


EXPENSIVE = frozenset(
    {
        "textual",
        "sqlalchemy",
        "alembic",
        "httpx",
        "rich",
        "flexi.app",
        "flexi.screens",
        "flexi.components",
        "flexi.models",
        "flexi.services.registry",
        "flexi.services.startup",
    }
)
"""What `flexi --version` must not pay for.

Measured: importing these took the entry point from 182 modules to 898, and
from 57 milliseconds to 637 -- before printing a string it already had.

The flexi entries matter as much as the third-party ones and are easier to miss,
because their root package is the cheap one. `from flexi.app import App` costs
every one of textual's 160 modules while looking local.
"""


def test_the_entry_point_stays_cheap_to_import() -> None:
    """The application is imported by the commands that open it, and no others.

    An AST check rather than `'textual' not in sys.modules`: the suite runs
    under `-n auto`, and a worker that has already run a Textual test has it
    loaded whatever this module does. Reading the imports asks the question
    that actually matters -- what does importing this file cost.
    """
    entry = SRC / "__main__.py"
    for module in module_scope_imports(entry):
        offending = next(
            (
                rule
                for rule in EXPENSIVE
                if module == rule or module.startswith(f"{rule}.")
            ),
            None,
        )
        assert offending is None, (
            f"__main__.py imports {module!r} at module scope, so every command "
            f"pays for it. Move it into the function that uses it."
        )


def test_the_type_checking_block_is_not_a_loophole() -> None:
    """`TYPE_CHECKING` imports are free, but only under the future import.

    Without `from __future__ import annotations` the annotations they type are
    evaluated at runtime, and the name is not there.
    """
    entry = SRC / "__main__.py"
    tree = ast.parse(entry.read_text(encoding="utf-8"), filename=str(entry))
    futures = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        for alias in node.names
    }
    assert "annotations" in futures


def declares_bindings(tree: ast.Module) -> Iterator[ast.ClassDef]:
    """Every class in a module whose body assigns ``BINDINGS``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        assigned = {
            target.id
            for statement in node.body
            for target in _targets(statement)
            if isinstance(target, ast.Name)
        }
        if "BINDINGS" in assigned:
            yield node


def _targets(statement: ast.stmt) -> Iterator[ast.expr]:
    if isinstance(statement, ast.AnnAssign):
        yield statement.target
    elif isinstance(statement, ast.Assign):
        yield from statement.targets


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: p.name)
def test_every_class_with_keys_says_what_to_call_it(path: Path) -> None:
    """A binding is filed in the help modal under its owner's `HELP_LABEL`.

    `label_for` used to look the class name up in a table and fall back to the
    class name itself, so a screen missing from the table filed its keys under
    `LeaveScreen` — which is what the leave screen and its calendar did, for
    eleven keys, silently. A fallback that looks like an answer needs something
    that refuses it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in declares_bindings(tree):
        labelled = {
            target.id
            for statement in node.body
            for target in _targets(statement)
            if isinstance(target, ast.Name)
        }
        assert "HELP_LABEL" in labelled, (
            f"{node.name} declares BINDINGS but no HELP_LABEL, so the help "
            f"modal would file its keys under {node.name!r}."
        )
