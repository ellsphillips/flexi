"""Book leave in one line, showing the plan back before it is written.

    flexi leave annual friday
    flexi leave annual monday to friday
    flexi leave sick today pm
    flexi leave cancel 12 jun

The head word names the kind of leave and comes from a closed vocabulary, so
everything after it is a date. Nothing is written until the plan has been shown
and agreed, which :meth:`~flexi.services.absence.AbsenceService.plan` allows by
deciding without committing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import Final, NamedTuple

import click

from flexi.constants import (
    CANCEL_WORD,
    AbsenceType,
    Portion,
    Verdict,
    absence_from_word,
)
from flexi.domain.dates import Preference, parse_span
from flexi.domain.format import days as fmt_days
from flexi.domain.format import long_date, plural, printable, short_date
from flexi.services.absence import AbsencePlan, RemovalBooking
from flexi.services.registry import Services, available_toil_days

__all__ = (
    "PORTION_WORDS",
    "VERDICT_NOTE",
    "Request",
    "cancel",
    "parse_request",
    "render",
    "run",
)

PORTION_WORDS: Final[Mapping[str, Portion]] = MappingProxyType(
    {
        "am": Portion.AM,
        "morning": Portion.AM,
        "pm": Portion.PM,
        "afternoon": Portion.PM,
    }
)
"""The words that name half a day.

``half`` is not one of them: it does not say which half, so it falls through to
the usage error an unrecognised date lands in.
"""

VERDICT_NOTE: Final[Mapping[Verdict, str]] = MappingProxyType(
    {
        Verdict.NON_WORKING: "not a working day",
        Verdict.BANK_HOLIDAY: "bank holiday",
    }
)


def _booking_line(booking: RemovalBooking) -> str:
    """One booked row, as the cancellation lists it."""
    portion = (
        "" if booking.portion is Portion.FULL else f" ({booking.portion.label.lower()})"
    )
    return f"  {short_date(booking.date)}   {booking.absence_type.label}{portion}"


class Request(NamedTuple):
    """``annual monday to friday pm``, split into what it asks for.

    ``kind`` is ``None`` for a cancellation, the one head word that names no
    kind of leave.
    """

    kind: AbsenceType | None
    portion: Portion | None
    """The requested portion, or ``None`` when none was written."""
    when: str


def parse_request(words: tuple[str, ...]) -> Request:
    """Split ``annual monday to friday pm`` into what it asks for.

    The portion is read from the last word only, so a note or a month name
    cannot be mistaken for one.
    """
    if not words:
        msg = "Say what kind of leave: annual, sick, toil, unpaid, other, or cancel"
        raise click.UsageError(msg)

    head, *rest = words
    word = head.strip().lower()
    kind = absence_from_word(word)
    if kind is None and word != CANCEL_WORD:
        msg = (
            f"'{head}' is not a kind of leave. "
            "Try annual, sick, toil, unpaid, other, or cancel."
        )
        raise click.UsageError(msg)

    portion: Portion | None = None
    if rest and rest[-1].lower() in PORTION_WORDS:
        portion = PORTION_WORDS[rest.pop().lower()]

    return Request(kind, portion, " ".join(rest))


def render(plan: AbsencePlan) -> str:
    """Return the plan as a block to check before agreeing to it."""
    verb = f"Booking {plan.absence_type.phrase}"
    portion = "" if plan.portion is Portion.FULL else f" ({plan.portion.label.lower()})"
    lines = [f"{verb}{portion}"]

    for day in plan.days:
        if day.verdict is Verdict.BOOK:
            lines.append(f"  {short_date(day.date)}")
        elif day.verdict.is_skip:
            note = printable(day.detail or VERDICT_NOTE.get(day.verdict, "skipped"))
            lines.append(f"  {short_date(day.date)}   — {note}")
        else:
            lines.append(f"  {short_date(day.date)}   ✗ {day.reason}")

    if plan.is_empty:
        lines.append("")
        lines.append("Nothing to do.")
        return "\n".join(lines)

    booked = len(plan.bookable)
    lines.append("")
    lines.append(f"{booked} {plural(booked, 'day')}, {fmt_days(plan.cost)} used")
    balances = tuple(
        balance
        for balance in plan.annual_balances
        if balance.before is not None and balance.after is not None
    )
    if plan.absence_type.draws_down_entitlement:
        for balance in balances:
            label = (
                "Annual leave" if len(balances) == 1 else f"Annual leave {balance.year}"
            )
            lines.append(
                f"{label}: {fmt_days(balance.before or 0)}"
                f" → {fmt_days(balance.after or 0)} "
                f"{plural(balance.after or 0, 'day')} left"
            )
    if plan.warning:
        lines.append(plan.warning)
    return "\n".join(lines)


def run(
    services: Services,
    words: tuple[str, ...],
    *,
    note: str | None,
    assume_yes: bool,
    dry_run: bool,
    today: date,
) -> int:
    """Plan, show, ask, write. Returns the exit code."""
    kind, requested_portion, when = parse_request(words)

    try:
        start, end = parse_span(
            when or "today", reference=today, prefer=Preference.FORWARD
        )
    except ValueError as error:
        raise click.UsageError(str(error)) from error

    if kind is None:
        return cancel(
            services,
            start,
            end,
            portion=requested_portion,
            assume_yes=assume_yes,
            dry_run=dry_run,
        )

    portion = requested_portion or Portion.FULL

    if kind is AbsenceType.OTHER and not (note or "").strip():
        msg = "Other leave needs --note saying what it is"
        raise click.UsageError(msg)

    plan = services.absence.plan(
        start,
        end,
        kind,
        portion,
        note=note,
        available_toil_days=available_toil_days(services, today),
    )
    click.echo(render(plan))

    if plan.is_empty:
        return 1
    if dry_run:
        return 0
    if not assume_yes and not click.confirm("\nBook it?", default=False, err=True):
        click.echo("Nothing was booked.", err=True)
        return 1

    result = services.absence.book_plan(plan)
    click.secho(
        result.message("booked"),
        fg="green" if result.success else "red",
        err=not result.success,
    )
    return 0 if result.success else 1


def cancel(
    services: Services,
    start: date,
    end: date,
    *,
    portion: Portion | None = None,
    assume_yes: bool,
    dry_run: bool,
) -> int:
    plan = services.absence.removal_plan(start, end, portion=portion)
    if plan.is_empty:
        span = (
            long_date(start)
            if start == end
            else f"{short_date(start)} to {long_date(end)}"
        )
        if portion is not None:
            # A half-day filter matches neither a full booking nor the other
            # half, so an empty result here does not mean the day is free.
            whole = services.absence.removal_plan(start, end)
            if not whole.is_empty:
                click.echo(
                    f"Nothing is booked for the {portion.label.lower()} "
                    f"of {span}. Booked there:",
                    err=True,
                )
                for booking in whole.bookings:
                    click.echo(_booking_line(booking), err=True)
                click.echo("Cancel the whole day to take it back.", err=True)
                return 1
        click.echo(f"Nothing is booked on {span}.", err=True)
        return 1

    click.echo("Cancelling")
    for booking in plan.bookings:
        click.echo(_booking_line(booking))

    if dry_run:
        return 0
    if not assume_yes and not click.confirm("\nCancel these?", default=False, err=True):
        click.echo("Nothing was cancelled.", err=True)
        return 1

    result = services.absence.remove_plan(plan)
    click.secho(
        result.message("cancelled"),
        fg="green" if result.success else "red",
        err=not result.success,
    )
    return 0 if result.success else 1
