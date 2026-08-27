"""Every text file is read and written as UTF-8, said out loud.

Python opens text files in the locale's encoding when it is not told otherwise.
On macOS and Linux that is UTF-8 and the omission never shows. On Windows it is
cp1252, and the first thing to arrive that cp1252 cannot spell is a
``UnicodeDecodeError`` from a line that has worked for years -- a config file
with a name in it, a note with an en dash, or this project's own README.

Ruff would say this, as ``PLW1514``, but only in preview mode, which with
``select = ALL`` means adopting every other preview rule at the same time. The
rule is worth more than the flood, so it is a test.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SEARCHED = ("src", "tests", "scripts")

OPENS_TEXT = frozenset({"open", "read_text", "write_text"})
"""Calls that take an ``encoding`` and quietly use the locale's without one."""


def _files() -> Iterator[Path]:
    for directory in SEARCHED:
        yield from sorted((ROOT / directory).rglob("*.py"))


def _called(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else ""


def _mode(node: ast.Call) -> str | None:
    """The mode an ``open`` call was given, positionally or by keyword.

    ``open(path, mode)`` carries it second and ``path.open(mode)`` carries it
    first. Reading the second argument for both meant every binary `Path.open`
    read as text. Only `open` is asked: `write_text(data)`'s first argument is
    the data.
    """
    if _called(node) != "open":
        return None
    at = 1 if isinstance(node.func, ast.Name) else 0
    given = [*node.args[at : at + 1]]
    given += [word.value for word in node.keywords if word.arg == "mode"]
    return next(
        (
            arg.value
            for arg in given
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ),
        None,
    )


def _is_binary(node: ast.Call) -> bool:
    """A mode with a ``b`` in it has no text to decode.

    Asked the way Python asks it, rather than against a list of the spellings
    somebody thought of: that list had nine entries and no ``a+b``, so the
    database lease -- which opens its lock file binary, where `encoding` would
    be a `TypeError` -- was reported as a Windows decoding bug.
    """
    mode = _mode(node)
    return mode is not None and "b" in mode


def _unencoded(source: Path) -> Iterator[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called(node) not in OPENS_TEXT:
            continue
        if _is_binary(node):
            continue
        if not any(word.arg == "encoding" for word in node.keywords):
            yield f"{source.relative_to(ROOT)}:{node.lineno}"


def test_no_text_file_is_opened_in_the_locale_encoding() -> None:
    """The rule holds over the scripts and the suite as well as the package.

    `scripts/shoot.py` writes the plain-text twin of every screenshot, and the
    suite reads the README and each source file to check them -- all of which
    contain characters cp1252 has no answer for, and none of which is code a
    user runs.
    """
    offenders = [place for source in _files() for place in _unencoded(source)]

    assert offenders == [], (
        "these open text without saying UTF-8, so they read as cp1252 on "
        f"Windows: {offenders}"
    )
