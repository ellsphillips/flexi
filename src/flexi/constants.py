from __future__ import annotations

from enum import StrEnum, auto


class StatusOption(StrEnum):
    """Actions for clocking in or out."""

    ARRIVE = auto()
    DEPART = auto()

    @classmethod
    def from_str(cls, action: str) -> StatusOption:
        """Convert a clock action to its equivalent Enum.

        Args:
            action: The clock action, i.e. IN or OUT.

        Returns:
            The equivalent of the clock action in Enum.

        Examples:
            >>> Clock.from_str("out") is Clock.OUT
            True
            >>> Clock.from_str("in") is Clock.IN
            True
            >>> Clock.from_str("o") is Clock.OUT
            True
        """
        return cls.ARRIVE if action.lower().startswith("a") else cls.DEPART
