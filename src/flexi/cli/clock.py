"""Clocking in and out from the command line.

Plain functions taking the service registry and returning an exit code, so they
can be called and asserted on without Click's test runner and without a
subprocess. `flexi.cli.leave` was already this shape; the decorator in
`__main__` is now a four-line adapter over each of these rather than the place
the work happens.
"""

from __future__ import annotations

import click

from flexi.services.outcome import Outcome
from flexi.services.registry import Services


def _report(result: Outcome) -> int:
    """Say what happened, and turn it into an exit code.

    Green and zero, or red and one. The same decision the status bar makes in
    the application, so the two surfaces agree about what counts as a failure.
    """
    click.secho(result.message, fg="green" if result.success else "red")
    return 0 if result.success else 1


def clock_in(services: Services) -> int:
    """Start a work session."""
    return _report(services.clock.clock_in())


def clock_out(services: Services) -> int:
    """End the current work session."""
    return _report(services.clock.clock_out())
