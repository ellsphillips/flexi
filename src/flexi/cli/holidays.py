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

from flexi.constants import DEFAULT_DIVISION
from flexi.services.registry import Services


def run(services: Services) -> int:
    """Fetch the calendar for the configured division. Returns an exit code."""
    division = services.settings.get_settings()
    named = division.bank_holiday_division if division else DEFAULT_DIVISION.value

    if not services.bank_holidays.fetch_and_cache():
        click.secho(
            f"Could not reach GOV.UK for {named}.\n"
            "Flexi keeps working; bank holidays will be missing until it can.",
            fg="yellow",
            err=True,
        )
        return 1

    dates = services.bank_holidays.get_dates() or set()
    click.secho(f"{len(dates)} bank holidays cached for {named}.", fg="green")
    return 0
