"""Reading and correcting the flexi balance from the command line.

Plain functions taking the service registry and returning an exit code, so they
can be called and asserted on without Click's test runner and without a
subprocess. The decorators in `__main__` are adapters over these.
"""

from __future__ import annotations

from datetime import date, timedelta

import click

from flexi import wallclock
from flexi.domain.format import delta, hm, long_date, stamp
from flexi.services.adjustments import OPENING_BALANCE
from flexi.services.registry import Services

NO_CALENDAR = (
    "\nNo bank holiday calendar: days off are counted as working days.\n"
    "Run `flexi holidays refresh` to fetch it."
)
"""Said under the balance, and only there.

Without a calendar every bank holiday is counted as a working day nobody
worked -- roughly eight days of deficit a leave year -- and this figure is the
only place that shows. Saying it before every command instead would be a warning
nobody can act on, printed where it has nothing to do with anything.
"""


def show(services: Services, as_of: date | None = None) -> int:
    """Print the running balance and what it is made of."""
    today = as_of or wallclock.today()
    start, _ = services.absence.leave_year_bounds(today)
    summary = services.ledger.balance(today)

    click.echo(
        f"leave year   {stamp(start, '%-d %b %Y')} → {stamp(today, '%-d %b %Y')}"
    )
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

    Records one signed adjustment rather than deleting anything: the clock
    events that produced the balance stay exactly where they are, and the line
    can be taken back with `flexi balance undo`.
    """
    when = as_of or wallclock.today() - timedelta(days=1)
    standing = services.ledger.balance(when).delta

    click.echo(f"balance as at {long_date(when)} is {delta(standing)}")
    if not assume_yes and not click.confirm("Settle it to zero?", default=True):
        click.echo("Left alone.")
        return 0

    result = services.zero_balance(when, reason=reason or OPENING_BALANCE)
    click.secho(result.message, fg="green" if result.success else "red")
    if not result.success:
        return 1

    now = services.ledger.balance(wallclock.today()).delta
    click.echo(f"balance now   {delta(now)}")
    return 0


def log(services: Services) -> int:
    """List every correction ever recorded."""
    rows = services.adjustments.all()
    if not rows:
        click.echo("No adjustments.")
    for row in rows:
        click.echo(
            f"{row.id:>4}  {row.date:%Y-%m-%d}  "
            f"{delta(timedelta(minutes=row.minutes)):>9}  {row.reason}"
        )
    return 0


def undo(services: Services, adjustment_id: int) -> int:
    """Remove a correction by its id, as listed by `log`."""
    result = services.adjustments.remove(adjustment_id)
    click.secho(result.message, fg="green" if result.success else "red")
    return 0 if result.success else 1
