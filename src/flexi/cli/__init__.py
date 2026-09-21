"""Command line surfaces that are too large to sit in __main__.

Each is a plain function taking the service registry and returning an exit
code, callable without Click and without a subprocess. The decorators in
`__main__` are adapters over these.
"""

from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import click

from flexi import wallclock
from flexi.services.outcome import Outcome

# These imports describe attributes that PEP 562 resolves lazily at runtime.
if TYPE_CHECKING:
    from flexi.cli import balance, clock, holidays, init, leave, output, ui
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
    from flexi.cli.output import (
        PLAIN_TERMINALS,
        enable_ansi,
        monochrome,
        prepare,
        tolerant,
    )

_SUBMODULES: Final = (
    "balance",
    "clock",
    "holidays",
    "init",
    "leave",
    "output",
    "ui",
)

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
        "PLAIN_TERMINALS": ("output", "PLAIN_TERMINALS"),
        "enable_ansi": ("output", "enable_ansi"),
        "monochrome": ("output", "monochrome"),
        "prepare": ("output", "prepare"),
        "tolerant": ("output", "tolerant"),
    }
)

# Keep the public surface grouped by its defining module.
__all__ = (  # noqa: RUF022
    "TypedDate",
    "Utf8Text",
    "report",
    "balance",
    "clock",
    "holidays",
    "init",
    "leave",
    "output",
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
    "PLAIN_TERMINALS",
    "enable_ansi",
    "monochrome",
    "prepare",
    "tolerant",
)


class TypedDate(click.ParamType[date]):
    """A date option, read with the grammar the rest of Flexi understands.

    Parameterised because `click.ParamType` is generic in its converted type
    from Click 8.5; the bare form is a mypy `type-arg` error under `strict`,
    and `date` is what Click's stubs then use for the option it is attached to.

    `click.DateTime` is not used: it accepts only `%Y-%m-%d` and hands back a
    `datetime`, which would give one command line two date grammars.
    """

    name = "date"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> date:
        # Imported here, not at module scope: the date grammar is most of what
        # importing `flexi.cli` costs, and `flexi --version` never parses one.
        from flexi.domain.dates import Preference, parse_date

        try:
            return parse_date(
                str(value), reference=wallclock.today(), prefer=Preference.CURRENT
            )
        except ValueError as error:
            self.fail(str(error), param, ctx)


class Utf8Text(click.ParamType[str]):
    """Free text the database can actually store.

    Python decodes ``argv`` with ``surrogateescape``, so a byte that is not
    UTF-8 arrives as a lone surrogate, which SQLite refuses to write.
    """

    name = "text"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        text = str(value)
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            self.fail("is not valid UTF-8 text", param, ctx)
        return text


def report(result: Outcome) -> int:
    """Say what happened, and turn it into an exit code.

    Green on stdout and zero, or red on stderr and one: a failure is not the
    program's output, so `flexi clock out >/dev/null` still shows its refusal.
    """
    click.secho(
        result.message,
        fg="green" if result.success else "red",
        err=not result.success,
    )
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
