"""Reading and correcting the flexi balance from the command line.

Plain functions taking the service registry and returning an exit code. The
decorators in `__main__` are adapters over these.
"""

from __future__ import annotations

from datetime import date, timedelta

import click

from flexi import wallclock
from flexi.cli import report, ui
from flexi.domain.format import delta, hm, long_date, printable, stamp
from flexi.services.adjustments import SETTLED, parse_amount
from flexi.services.registry import (
    Services,
    adjust_balance,
    adjustment_refusal,
    settlement_date,
    zero_balance,
)

__all__ = ("NO_CALENDAR", "adjust", "log", "show", "undo", "zero")

NO_CALENDAR = (
    "\nNo bank holiday calendar: days off are counted as working days.\n"
    "Run `flexi holidays refresh` to fetch it."
)
"""Said under the balance, and only there.

Without a calendar every bank holiday is counted as an unworked working day,
about eight days of deficit a leave year, and the balance is the only figure
that shows it.
"""


def show(services: Services, as_of: date | None = None) -> int:
    """Print the running balance and what it is made of."""
    now = wallclock.today()
    today = as_of or now
    if today > now:
        # A future date charges every working day between now and then as
        # unworked, so the figure would be a deficit of days not yet lived.
        click.secho(
            f"{long_date(today)} has not happened; the balance runs to today",
            fg="yellow",
            err=True,
        )
        return 1

    start, _ = services.absence.leave_year_bounds(today)
    summary = services.ledger.balance(today).as_shown()

    click.echo(
        f"leave year   {stamp(start, '%-d %b %Y')} → {stamp(today, '%-d %b %Y')}"
    )
    # Shown only when the tracking date falls inside the reported span, where
    # it explains why `expected` covers less of the leave year than the dates do.
    since = services.settings.resolved().tracking_since
    if since is not None and start < since <= today:
        click.echo(f"tracking     {stamp(since, '%-d %b %Y')} onwards")
    click.echo(f"worked       {hm(summary.worked)}")
    click.echo(f"expected     {hm(summary.expected)}")
    if summary.toil_taken:
        click.echo(f"toil taken   {hm(summary.toil_taken)}")
    if summary.adjustment:
        click.echo(f"adjusted     {delta(summary.adjustment)}")
    click.secho(f"balance      {delta(summary.delta)}", bold=True)

    if not services.bank_holidays.is_available():
        click.secho(NO_CALENDAR, fg="yellow", err=True)
    return 0


def zero(
    services: Services,
    as_of: date | None = None,
    reason: str | None = None,
    *,
    assume_yes: bool = False,
) -> int:
    """Draw a line under everything up to a date.

    Records one signed adjustment and deletes nothing, so the clock events that
    produced the balance stay where they are and `flexi balance undo` can take
    the line back. Declining exits 1, as declining a booking does, so a script
    chaining on `&&` can tell the write did not happen.
    """
    when = settlement_date(as_of)
    if when >= wallclock.today():
        # `zero_balance` refuses a future date, and the standing it would be
        # sized from counts every day between now and then as unworked.
        return report(zero_balance(services, when, reason=reason or SETTLED))

    standing = services.ledger.balance(when).delta

    click.echo(f"balance as at {long_date(when)} is {delta(standing)}")
    if not assume_yes and not click.confirm(
        "Settle it to zero?", default=True, err=True
    ):
        click.echo("Left alone.", err=True)
        return 1

    result = zero_balance(services, when, reason=reason or SETTLED)
    if report(result):
        return 1

    now = services.ledger.balance(wallclock.today()).delta
    click.echo(f"balance now   {delta(now)}")
    return 0


def adjust(
    services: Services,
    amount: str,
    reason: str | None,
    on: date | None = None,
    *,
    assume_yes: bool = False,
) -> int:
    """Move the balance by a signed amount, showing the balance it makes first.

    Declining exits 1, as declining a settlement does. With no terminal to ask
    on, it refuses unless `--yes` was given: a pipe is not someone answering,
    and one left open would never answer.
    """
    try:
        change = parse_amount(amount)
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    why = (reason or "").strip()
    if not why:
        msg = "An adjustment needs a reason: say why with --reason"
        raise click.UsageError(msg)

    today = wallclock.today()
    when = on or today
    refusal = adjustment_refusal(services, when)
    if refusal is not None:
        click.secho(refusal, fg="red", err=True)
        return 1

    before = services.ledger.balance(today).as_shown().delta
    click.echo(f"Adjusting the balance by {delta(change)} on {long_date(when)}")
    click.echo(f"  {printable(why)}")
    click.echo(f"\nBalance: {delta(before)} → {delta(before + change)}")

    if not assume_yes:
        if not ui.interactive():
            click.secho(
                "No terminal to ask on; add --yes to record it without asking.",
                fg="yellow",
                err=True,
            )
            return 1
        if not click.confirm("\nRecord it?", default=True, err=True):
            click.echo("Nothing was recorded.", err=True)
            return 1

    return report(adjust_balance(services, change, why, when))


def log(services: Services) -> int:
    """List every correction ever recorded."""
    rows = services.adjustments.all()
    if not rows:
        click.echo("No adjustments.")
    for row in rows:
        click.echo(
            f"{row.id:>4}  {row.date:%Y-%m-%d}  "
            f"{delta(timedelta(minutes=row.minutes)):>9}  {printable(row.reason)}"
        )
    return 0


def undo(services: Services, adjustment_id: int) -> int:
    """Remove a correction by its id, as listed by `log`."""
    return report(services.adjustments.remove(adjustment_id))
