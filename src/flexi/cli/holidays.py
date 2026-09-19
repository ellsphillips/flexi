"""Refreshing the bank holiday calendar from the command line.

Opening the database fills an empty cache on its own. This command is for the
other two cases: a calendar that has gone stale, and a year that has just been
published.
"""

from __future__ import annotations

import click

from flexi.services.registry import Services

__all__ = ("run",)


def run(services: Services) -> int:
    """Fetch the calendar for the configured division. Returns an exit code.

    A failed fetch is always a non-zero exit, even when a cached calendar
    survives, so a scheduled run can tell the refresh did not happen; what the
    failure says depends on whether a calendar is left to fall back on. This
    command opens with ``fill=False``, because its own fetch is that fill.
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
