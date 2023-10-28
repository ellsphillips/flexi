from abc import ABC, abstractmethod
from pathlib import Path

from flexi.constants import Clock
from flexi.log.model import Log
from flexi.types import Leave


class Registrar(ABC):
    """Interface for communicating recordings to and from the flexi log."""

    path: Path
    log: Log

    @abstractmethod
    def record_clock(self, clock: Clock) -> None:
        """Record a session action."""

    def record_leave(self, leave: Leave) -> None:
        """Record a leave action."""
        raise NotImplementedError
