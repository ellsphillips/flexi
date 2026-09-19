"""Clocking in and out from the command line.

Plain functions taking the service registry and returning an exit code. The
decorators in `__main__` are adapters over these.
"""

from __future__ import annotations

from datetime import datetime

from rich.console import Console

from flexi import wallclock
from flexi.cli import report
from flexi.cli.ui.onclock import on_the_clock
from flexi.models.database.moment import moment_of
from flexi.services.registry import Services

__all__ = ("already_on", "clock_in", "clock_out")


def clock_in(services: Services) -> int:
    """Start a work session."""
    result = services.clock.clock_in()
    if not result.success and result.session is not None:
        return already_on(services, moment_of(result.session.clock_in_event))
    return report(result)


def clock_out(services: Services) -> int:
    """End the current work session."""
    return report(services.clock.clock_out())


def already_on(services: Services, since: datetime) -> int:
    """Draw the running session on stdout, and return a non-zero exit code.

    Printed through Rich, not `click.echo`, which stringifies a `Text` to its
    plain characters and leaves the punch strip and the signed balance in
    default ink. Rich withholds the styles itself when stdout is not a
    terminal, so a piped run stays plain.
    """
    now = wallclock.now()
    today = now.date()
    ledger = services.ledger.days(today, today, now=now)[0]
    balance = services.ledger.balance(today, now=now).delta
    strip = on_the_clock(ledger, services.ledger.window, since, balance, now=now)
    console = Console(highlight=False, markup=False, emoji=False)
    console.print()
    console.print(strip, soft_wrap=True)
    console.print()
    return 1
