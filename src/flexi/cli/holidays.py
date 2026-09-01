"""Refreshing the bank holiday calendar from the command line.

Nothing in the application ever filled the cache. The only route to a populated
one was an entry in the Textual command palette, so somebody who used Flexi
entirely from the command line could not reach it -- and an empty cache is not
a quiet failure. Every leave booking is refused, and every bank holiday is
counted as a working day nobody worked.

Startup fills an empty cache on its own now. This is for the other two cases:
a calendar that has gone stale, and a year that has just been published.
"""

from __future__ import annotations

import click

from flexi.services.registry import Services

__all__ = ("run",)


def run(services: Services) -> int:
    """Fetch the calendar for the configured division. Returns an exit code.

    A failed fetch is always a non-zero exit: the refresh is what was asked for,
    a cron entry reads the code, and softening it to zero whenever some calendar
    survives would mean a machine could go a year without a successful refresh
    and never once say so.

    What the failure *says* depends on whether anything is left to fall back on.
    It claimed "bank holidays will be missing" either way, which is untrue of
    the commoner case by far -- every command runs `fill_if_empty` on the way
    in, so by the time this runs there is usually a calendar, and a stale
    calendar still answers correctly for the year it holds.
    """
    named = services.bank_holidays.division.label

    if not services.bank_holidays.fetch_and_cache():
        kept = (
            "The calendar already cached is unchanged and still in use."
            if services.bank_holidays.is_available()
            else "Flexi keeps working; bank holidays will be missing until it can."
        )
        click.secho(
            f"Could not reach GOV.UK for {named}.\n{kept}", fg="yellow", err=True
        )
        return 1

    dates = services.bank_holidays.get_dates() or set()
    click.secho(f"{len(dates)} bank holidays cached for {named}.", fg="green")
    return 0
