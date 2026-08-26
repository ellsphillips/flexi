"""Clocking in and out from the command line.

Plain functions taking the service registry and returning an exit code, so they
can be called and asserted on without Click's test runner and without a
subprocess. `flexi.cli.leave` was already this shape; the decorator in
`__main__` is now a four-line adapter over each of these rather than the place
the work happens.
"""

from __future__ import annotations

from flexi.cli import report
from flexi.services.registry import Services


def clock_in(services: Services) -> int:
    """Start a work session."""
    return report(services.clock.clock_in())


def clock_out(services: Services) -> int:
    """End the current work session."""
    return report(services.clock.clock_out())
