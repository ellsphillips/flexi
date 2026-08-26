"""Clocking in and out from the command line.

Plain functions taking the service registry and returning an exit code, so they
can be called and asserted on without Click's test runner and without a
subprocess.
"""

from __future__ import annotations

from datetime import datetime

import click

from flexi import wallclock
from flexi.cli import report
from flexi.cli.ui.onclock import on_the_clock
from flexi.models.database.moment import moment_of
from flexi.services.registry import Services


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
    """Draw the running session rather than refusing in one red line."""
    now = wallclock.now()
    today = now.date()
    ledger = services.ledger.days(today, today, now=now)[0]
    balance = services.ledger.balance(today, now=now).delta
    click.echo()
    click.echo(on_the_clock(ledger, services.ledger.window, since, balance, now=now))
    click.echo()
    return 1
