import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import rich

from flexi.constants import Clock
from flexi.log.model import Day, Log, Session
from flexi.types import Leave
from flexi.utils import get_current_time, print_successful_clock_message


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


@dataclass
class JSONRegistrar:
    """Registrar that stores the log as JSON."""

    path: Path
    log: Log

    def record_clock(self, clock: Clock) -> None:
        """Record a session action."""
        arrive(self.log) if clock is Clock.IN else depart(self.log)

        self.log.days.sort(key=lambda d: d.date)

        with Path.open(self.path, "w") as f:
            json.dump([d.model_dump() for d in self.log.days], f, indent=2, default=str)


def currently_clocked_in(day: Day) -> bool:
    """Check if the user is able to clock in."""
    return all(
        [
            # Check there are sessions initiated yet today.
            day.sessions,
            # Check if the latest session has a clock in time.
            day.sessions and day.sessions[-1].clock_in,
            # Check if the latest session has no clock out time.
            day.sessions and not day.sessions[-1].clock_out,
        ]
    )


def currently_clocked_out(day: Day) -> bool:
    """Check if the user is able to clock out."""
    return any(
        [
            # Check there are no sessions initiated yet today.
            not day.sessions,
            # Check if the latest session has a clock out time.
            day.sessions and day.sessions[-1].clock_out,
        ]
    )


def arrive(log: Log) -> None:
    """Handle the user arriving at work."""
    date = datetime.now().date()  # noqa: DTZ005
    day = log[date.isoformat()]
    now = get_current_time()

    if currently_clocked_in(day):
        rich.print("[b purple]You are already clocked in.[/]")
        return

    day.sessions.append(Session(clock_in=now, clock_out=""))

    print_successful_clock_message(Clock.IN)


def depart(log: Log) -> None:
    """Handle the user departing work."""
    date = datetime.now().date()  # noqa: DTZ005
    day = log[date.isoformat()]
    now = get_current_time()

    if currently_clocked_out(day):
        rich.print("[b purple]No active session found. Please clock in first.[/]")
        return

    day.sessions[-1].clock_out = now

    print_successful_clock_message(Clock.OUT)
