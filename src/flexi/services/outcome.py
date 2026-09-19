"""What a service result tells the interface.

The status bar goes green or red on ``success`` alone, so the results that
reach it share one protocol and `--strict` checks the shape.

Read-only properties, not variables: every implementer is a frozen dataclass,
and a protocol asking for a settable attribute is not satisfied by one that
cannot be set. `RangeResult` is not an implementer; it is partial by design, a
fortnight that books nine days and says which five it skipped, and it never
reaches the status bar.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ("Outcome",)


@runtime_checkable
class Outcome(Protocol):
    """Something that happened, and what to tell the user about it."""

    @property
    def success(self) -> bool:
        """Whether the thing asked for was done."""
        ...

    @property
    def message(self) -> str:
        """One line, in the past tense, for a status bar."""
        ...

    @property
    def warning(self) -> str | None:
        """Said instead of the message when it succeeded with a caveat.

        Every implementer carries one, so no caller has to ask whether this
        attribute is there.
        """
        ...
