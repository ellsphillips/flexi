"""Command line surfaces that are too large to sit in __main__.

Each is a plain function taking the service registry and returning an exit
code, so it can be called and asserted on without Click's test runner and
without a subprocess. The decorators in `__main__` are adapters over these.
"""

from __future__ import annotations

from datetime import date

import click

from flexi import wallclock
from flexi.domain.dates import Preference, parse_date
from flexi.services.outcome import Outcome


class TypedDate(click.ParamType):
    """A date option, read with the grammar the rest of Flexi understands.

    `click.DateTime` accepts `%Y-%m-%d` and hands back a `datetime`, so every
    option using it had to unwrap `.date()` and declare a parameter as a
    `datetime` that was never anything but a date. It also left
    `flexi balance show --as-of friday` a usage error while
    `flexi leave annual friday` worked -- one command line, two date grammars.
    """

    name = "date"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> date:
        try:
            return parse_date(
                str(value), reference=wallclock.today(), prefer=Preference.CURRENT
            )
        except ValueError as error:
            self.fail(str(error), param, ctx)


def report(result: Outcome) -> int:
    """Say what happened, and turn it into an exit code.

    Green and zero, or red and one. The same decision the status bar makes in
    the application, so the two surfaces agree about what counts as a failure
    -- and one decision rather than the three copies that were spread across
    two modules, of which only one carried the reason.
    """
    click.secho(result.message, fg="green" if result.success else "red")
    return 0 if result.success else 1
