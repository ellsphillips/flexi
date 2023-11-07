from enum import Enum
from pathlib import Path

HOME_DIR = Path.home() / ".flexi/"
DEV_HOME_DIR = Path().cwd() / ".flexi"


class Clock(str, Enum):
    """Actions for clocking in or out."""

    IN = "in"
    OUT = "out"

    @classmethod
    def from_str(cls, action: str) -> "Clock":
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
        return cls.OUT if action.lower().startswith("o") else cls.IN
