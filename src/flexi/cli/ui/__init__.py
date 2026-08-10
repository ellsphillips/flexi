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

from flexi.cli.ui.keys import Key, decode
from flexi.cli.ui.menu import Menu, Option
from flexi.cli.ui.prompt import (
    abandon,
    choose,
    console,
    interactive,
    type_the_word,
    write,
)
from flexi.cli.ui.rail import Tone, body, measure, step, tail, wordmark

__all__ = [
    "Key",
    "Menu",
    "Option",
    "Tone",
    "abandon",
    "body",
    "choose",
    "console",
    "decode",
    "interactive",
    "measure",
    "step",
    "tail",
    "type_the_word",
    "wordmark",
    "write",
]
