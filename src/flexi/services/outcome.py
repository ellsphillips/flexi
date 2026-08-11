"""What a service result tells the interface.

Four dataclasses flow out of the services and three of them end up on the status
bar, which is the single decision about whether it goes green or red. That
decision was made with `getattr(result, "success", False)` against a parameter
typed `object`, because the four shapes did not agree -- so the one place it
mattered was the one place `--strict` could not check, and a renamed field would
have reported failure rather than failing to compile.

Declared as read-only properties, not as variables: every implementer is a
frozen dataclass, and a protocol asking for a settable attribute is not
satisfied by one that cannot be set.

`RangeResult` is deliberately not one of these. It is partial by design -- a
fortnight that books twelve days and says which two it skipped -- and its
message takes the verb that describes what was attempted, which is a different
shape for a different job. It never reaches the status bar.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Outcome(Protocol):
    """Something that happened, and what to tell somebody about it."""

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

        Carried by every implementer rather than by some, so the interface does
        not have to ask whether asking is allowed.
        """
        ...
