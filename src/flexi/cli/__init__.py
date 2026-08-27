"""Command line surfaces that are too large to sit in __main__.

Each is a plain function taking the service registry and returning an exit
code, so it can be called and asserted on without Click's test runner and
without a subprocess. The decorators in `__main__` are adapters over these.
"""

from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import click

from flexi import wallclock
from flexi.domain.dates import Preference, parse_date
from flexi.services.outcome import Outcome

# These imports describe attributes that PEP 562 resolves lazily at runtime.
# ruff: noqa: TC004
if TYPE_CHECKING:
    from flexi.cli import balance, clock, holidays, init, leave, ui
    from flexi.cli.balance import NO_CALENDAR, log, show, undo, zero
    from flexi.cli.clock import already_on, clock_in, clock_out
    from flexi.cli.holidays import run as refresh_holidays
    from flexi.cli.init import (
        CONFIRM_WORD,
        COUNTED,
        READ_TIMEOUT,
        Choice,
        Contents,
        ask,
        confirm_reset,
        describe,
        options,
        overview,
        reset,
        settled,
    )
    from flexi.cli.leave import (
        PORTION_WORDS,
        VERDICT_NOTE,
        Request,
        cancel,
        parse_request,
        render,
    )
    from flexi.cli.leave import (
        run as manage_leave,
    )

_SUBMODULES: Final = ("balance", "clock", "holidays", "init", "leave", "ui")

_EXPORTS: Final = MappingProxyType(
    {
        "NO_CALENDAR": ("balance", "NO_CALENDAR"),
        "log": ("balance", "log"),
        "show": ("balance", "show"),
        "undo": ("balance", "undo"),
        "zero": ("balance", "zero"),
        "already_on": ("clock", "already_on"),
        "clock_in": ("clock", "clock_in"),
        "clock_out": ("clock", "clock_out"),
        "refresh_holidays": ("holidays", "run"),
        "CONFIRM_WORD": ("init", "CONFIRM_WORD"),
        "COUNTED": ("init", "COUNTED"),
        "Choice": ("init", "Choice"),
        "Contents": ("init", "Contents"),
        "READ_TIMEOUT": ("init", "READ_TIMEOUT"),
        "ask": ("init", "ask"),
        "confirm_reset": ("init", "confirm_reset"),
        "describe": ("init", "describe"),
        "options": ("init", "options"),
        "overview": ("init", "overview"),
        "reset": ("init", "reset"),
        "settled": ("init", "settled"),
        "PORTION_WORDS": ("leave", "PORTION_WORDS"),
        "Request": ("leave", "Request"),
        "VERDICT_NOTE": ("leave", "VERDICT_NOTE"),
        "cancel": ("leave", "cancel"),
        "manage_leave": ("leave", "run"),
        "parse_request": ("leave", "parse_request"),
        "render": ("leave", "render"),
    }
)

# Keep the public surface grouped by its defining module.
__all__ = (  # noqa: RUF022
    "TypedDate",
    "report",
    "balance",
    "clock",
    "holidays",
    "init",
    "leave",
    "ui",
    "NO_CALENDAR",
    "log",
    "show",
    "undo",
    "zero",
    "already_on",
    "clock_in",
    "clock_out",
    "refresh_holidays",
    "CONFIRM_WORD",
    "COUNTED",
    "Choice",
    "Contents",
    "READ_TIMEOUT",
    "ask",
    "confirm_reset",
    "describe",
    "options",
    "overview",
    "reset",
    "settled",
    "PORTION_WORDS",
    "Request",
    "VERDICT_NOTE",
    "cancel",
    "manage_leave",
    "parse_request",
    "render",
)


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


def __getattr__(name: str) -> object:
    """Import and cache one command module or public value on first access."""
    if name in _SUBMODULES:
        module_name, attribute = f"{__name__}.{name}", None
    elif route := _EXPORTS.get(name):
        module, attribute = route
        module_name = f"{__name__}.{module}"
    else:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    from importlib import import_module

    imported = import_module(module_name)
    resolved = imported if attribute is None else getattr(imported, attribute)
    globals()[name] = resolved
    return resolved


def __dir__() -> list[str]:
    """Include unresolved facade exports in interactive discovery."""
    return sorted(set(globals()) | set(__all__))
