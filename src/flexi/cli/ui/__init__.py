"""Flexi's own terminal prompts.

Click can ask a question and Textual can draw an application. Between them sits
what ``flexi init`` actually needs: a few lines that hold their shape, answer to
the arrow keys, and read as the same product as the screen that opens a moment
later. Nothing on PyPI draws that in Flexi's language, so this does -- in about
three hundred lines, with no dependency Flexi did not already have.

The split is the one the splash animation uses. ``keys``, ``rail`` and ``menu``
are pure: they turn arguments into values and renderables, and the suite drives
them by pressing keys into a dataclass. ``prompt`` is the only part that knows a
terminal exists, and it is the only part that cannot be tested without one.

    from flexi.cli import ui

    picked = ui.choose("What would you like to do?", [
        ui.Option("open", "Open Flexi", "your records, as they are"),
        ui.Option("reset", "Start again", "erase everything", grave=True),
    ])
"""

from __future__ import annotations

# These imports describe attributes that PEP 562 resolves lazily at runtime.
# ruff: noqa: TC004
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from flexi.cli.ui import keys, menu, onclock, prompt, rail
    from flexi.cli.ui.keys import ESCAPE, KEYS, PREFIXES, Key, decode, incomplete
    from flexi.cli.ui.menu import HINT, Menu, Option
    from flexi.cli.ui.onclock import (
        CELL_TONES,
        STRIP_CELLS,
        elapsed_since,
        on_the_clock,
        punch_line,
    )
    from flexi.cli.ui.prompt import (
        ESCAPE_WAIT,
        WINDOWS_PREFIXES,
        WINDOWS_SCANCODES,
        Surface,
        abandon,
        choose,
        console,
        interactive,
        more_coming,
        read_key,
        read_posix,
        read_windows,
        type_the_word,
        unbuffered,
        write,
    )
    from flexi.cli.ui.rail import (
        ACTIVE,
        ALERT,
        GUTTER,
        HAIRLINE,
        HEAVY,
        LABEL_WIDTH,
        SETTLED,
        Tone,
        body,
        measure,
        option,
        rail_line,
        step,
        tail,
        wordmark,
    )

_SUBMODULES: Final = ("keys", "menu", "onclock", "prompt", "rail")

_EXPORTS: Final = MappingProxyType(
    {
        "ESCAPE": ("keys", "ESCAPE"),
        "KEYS": ("keys", "KEYS"),
        "Key": ("keys", "Key"),
        "PREFIXES": ("keys", "PREFIXES"),
        "decode": ("keys", "decode"),
        "incomplete": ("keys", "incomplete"),
        "HINT": ("menu", "HINT"),
        "Menu": ("menu", "Menu"),
        "Option": ("menu", "Option"),
        "CELL_TONES": ("onclock", "CELL_TONES"),
        "STRIP_CELLS": ("onclock", "STRIP_CELLS"),
        "elapsed_since": ("onclock", "elapsed_since"),
        "on_the_clock": ("onclock", "on_the_clock"),
        "punch_line": ("onclock", "punch_line"),
        "ESCAPE_WAIT": ("prompt", "ESCAPE_WAIT"),
        "Surface": ("prompt", "Surface"),
        "WINDOWS_PREFIXES": ("prompt", "WINDOWS_PREFIXES"),
        "WINDOWS_SCANCODES": ("prompt", "WINDOWS_SCANCODES"),
        "abandon": ("prompt", "abandon"),
        "choose": ("prompt", "choose"),
        "console": ("prompt", "console"),
        "interactive": ("prompt", "interactive"),
        "more_coming": ("prompt", "more_coming"),
        "read_key": ("prompt", "read_key"),
        "read_posix": ("prompt", "read_posix"),
        "read_windows": ("prompt", "read_windows"),
        "type_the_word": ("prompt", "type_the_word"),
        "unbuffered": ("prompt", "unbuffered"),
        "write": ("prompt", "write"),
        "ACTIVE": ("rail", "ACTIVE"),
        "ALERT": ("rail", "ALERT"),
        "GUTTER": ("rail", "GUTTER"),
        "HAIRLINE": ("rail", "HAIRLINE"),
        "HEAVY": ("rail", "HEAVY"),
        "LABEL_WIDTH": ("rail", "LABEL_WIDTH"),
        "SETTLED": ("rail", "SETTLED"),
        "Tone": ("rail", "Tone"),
        "body": ("rail", "body"),
        "measure": ("rail", "measure"),
        "option": ("rail", "option"),
        "rail_line": ("rail", "rail_line"),
        "step": ("rail", "step"),
        "tail": ("rail", "tail"),
        "wordmark": ("rail", "wordmark"),
    }
)

# Keep the public surface grouped by its defining module.
__all__ = (  # noqa: RUF022
    "keys",
    "menu",
    "onclock",
    "prompt",
    "rail",
    "ESCAPE",
    "KEYS",
    "Key",
    "PREFIXES",
    "decode",
    "incomplete",
    "HINT",
    "Menu",
    "Option",
    "CELL_TONES",
    "STRIP_CELLS",
    "elapsed_since",
    "on_the_clock",
    "punch_line",
    "ESCAPE_WAIT",
    "Surface",
    "WINDOWS_PREFIXES",
    "WINDOWS_SCANCODES",
    "abandon",
    "choose",
    "console",
    "interactive",
    "more_coming",
    "read_key",
    "read_posix",
    "read_windows",
    "type_the_word",
    "unbuffered",
    "write",
    "ACTIVE",
    "ALERT",
    "GUTTER",
    "HAIRLINE",
    "HEAVY",
    "LABEL_WIDTH",
    "SETTLED",
    "Tone",
    "body",
    "measure",
    "option",
    "rail_line",
    "step",
    "tail",
    "wordmark",
)


def __getattr__(name: str) -> object:
    """Import and cache one UI module or public value on first access."""
    if name in _SUBMODULES:
        module_name, attribute = f"{__name__}.{name}", None
    elif route := _EXPORTS.get(name):
        module, attribute = route
        module_name = f"{__name__}.{module}"
    else:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    from importlib import import_module

    imported = import_module(module_name)
    resolved = imported if attribute is None else getattr(imported, attribute)
    globals()[name] = resolved
    return resolved


def __dir__() -> list[str]:
    """Include unresolved facade exports in interactive discovery."""
    return sorted(set(globals()) | set(__all__))
