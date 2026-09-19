"""Every text file is read and written as UTF-8.

Without an ``encoding`` argument Python uses the locale's, which is cp1252 on
Windows: any character it cannot spell raises ``UnicodeDecodeError`` there and
nowhere else. Ruff's ``PLW1514`` says the same thing but only in preview mode,
which under ``select = ALL`` would enable every other preview rule too.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SEARCHED = ("src", "tests", "scripts")

OPENS_TEXT = frozenset({"open", "read_text", "write_text"})
"""Calls that take an ``encoding`` and fall back to the locale's without one."""


def _files() -> Iterator[Path]:
    for directory in SEARCHED:
        yield from sorted((ROOT / directory).rglob("*.py"))


def _called(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else ""


def _mode(node: ast.Call) -> str | None:
    """Return the mode an ``open`` call was given, positionally or by keyword.

    ``open(path, mode)`` carries it second, ``path.open(mode)`` first. Only
    ``open`` is asked, because ``write_text(data)``'s first argument is data.
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
    """Report whether the mode has a ``b`` in it, so there is no text to decode.

    Tested the way Python tests it. A list of mode spellings misses ``a+b``,
    and a binary open takes no ``encoding``: passing one is a ``TypeError``.
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


def test_text_files_are_opened_with_an_encoding() -> None:
    offenders = [place for source in _files() for place in _unencoded(source)]

    assert offenders == [], (
        "these open text without saying UTF-8, so they read as cp1252 on "
        f"Windows: {offenders}"
    )
