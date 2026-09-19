"""A chooser held as a value.

The menu is immutable: a key press returns a new menu, and drawing is a
function of the one in hand, so the interaction needs no terminal.
"""

from dataclasses import dataclass, replace
from typing import Final, Self

from rich.text import Text

from flexi.cli.ui import rail
from flexi.cli.ui.keys import Key

__all__ = ("HINT", "Menu", "Option")

HINT: Final = "↑↓ move · ↵ choose · esc cancel"


@dataclass(frozen=True, slots=True)
class Option[ValueT]:
    """A row of a chooser."""

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

    def press(self, key: Key) -> Self:
        """The menu after a key press, with the cursor wrapping at both ends."""
        if key is Key.UP:
            return replace(self, cursor=(self.cursor - 1) % len(self.options))
        if key is Key.DOWN:
            return replace(self, cursor=(self.cursor + 1) % len(self.options))
        return self

    def render(self) -> list[Text]:
        """The chooser's lines, top to bottom."""
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
