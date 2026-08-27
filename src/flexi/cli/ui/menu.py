"""A chooser, as a value rather than a loop.

The menu is immutable: a key press returns a new menu, and drawing is a function
of the one you hold. That makes the whole interaction testable by pressing keys
into it and reading what comes back, with no terminal, no threads and no
sleeping -- the same trick the splash animation uses to stay out of the suite's
running time.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from rich.text import Text

from flexi.cli.ui import rail
from flexi.cli.ui.keys import Key

HINT: Final = "↑↓ move · ↵ choose · esc cancel"


@dataclass(frozen=True, slots=True)
class Option[ValueT]:
    """One row of a chooser."""

    value: ValueT
    label: str
    hint: str = ""
    grave: bool = False
    """Whether taking this loses something. Drawn in the deficit red."""


@dataclass(frozen=True, slots=True)
class Menu[ValueT]:
    """A question and the answers to it, with one of them under the cursor."""

    question: str
    options: tuple[Option[ValueT], ...]
    cursor: int = 0

    def __post_init__(self) -> None:
        if not self.options:
            msg = "a menu needs at least one option"
            raise ValueError(msg)

    @property
    def picked(self) -> Option[ValueT]:
        return self.options[self.cursor]

    def press(self, key: Key) -> Menu[ValueT]:
        """The menu after a key press.

        Wraps at both ends. A list this short is quicker to reach round the back
        of than to run to the end of.
        """
        if key is Key.UP:
            return replace(self, cursor=(self.cursor - 1) % len(self.options))
        if key is Key.DOWN:
            return replace(self, cursor=(self.cursor + 1) % len(self.options))
        return self

    def render(self) -> list[Text]:
        """Every line of the chooser, top to bottom."""
        lines = [rail.step(self.question), rail.body(tone=rail.Tone.LIVE)]
        lines.extend(
            rail.option(
                choice.label,
                choice.hint,
                picked=index == self.cursor,
                grave=choice.grave,
            )
            for index, choice in enumerate(self.options)
        )
        lines.append(rail.body(tone=rail.Tone.LIVE))
        lines.append(rail.tail(HINT))
        return lines
