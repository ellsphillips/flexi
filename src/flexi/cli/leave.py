"""Booking leave in one line, and showing it back before it is written.

    flexi leave annual friday
    flexi leave annual monday to friday
    flexi leave sick today pm
    flexi leave cancel 12 jun

The first word is what kind, the rest is when. That order is the one people say
out loud, and it is the one that stays unambiguous: the type comes from a closed
vocabulary, so everything after it is a date and nothing has to be guessed.

Nothing is written until the plan has been shown and agreed. That is only
possible because :meth:`~flexi.services.absence.AbsenceService.plan` decides
without committing -- booking used to write day by day, so a prompt built from
the result was a receipt.
"""

from __future__ import annotations

from datetime import date

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
from flexi.domain.format import long_date, short_date
from flexi.services.absence import AbsencePlan
from flexi.services.registry import Services

PORTION_WORDS: dict[str, Portion] = {
    "am": Portion.AM,
    "morning": Portion.AM,
    "pm": Portion.PM,
    "afternoon": Portion.PM,
    "half": Portion.AM,
}

VERDICT_NOTE: dict[Verdict, str] = {
    Verdict.NON_WORKING: "not a working day",
    Verdict.BANK_HOLIDAY: "bank holiday",
}


def parse_request(words: tuple[str, ...]) -> tuple[str, Portion, str]:
    """Split ``annual monday to friday pm`` into its three parts.

    Returns the head word, the portion, and the remaining date text. The
    portion is taken off the end rather than looked for anywhere, so a note or
    a month name cannot be mistaken for one.
    """
    if not words:
        msg = "Say what kind of leave: annual, sick, toil, unpaid, other, or cancel"
        raise click.UsageError(msg)

    head, *rest = words
    known = head.lower() == CANCEL_WORD or absence_from_word(head) is not None
    if not known:
        msg = (
            f"'{head}' is not a kind of leave. "
            "Try annual, sick, toil, unpaid, other, or cancel."
        )
        raise click.UsageError(msg)

    portion = Portion.FULL
    if rest and rest[-1].lower() in PORTION_WORDS:
        portion = PORTION_WORDS[rest.pop().lower()]

    return head.lower(), portion, " ".join(rest)


def render(plan: AbsencePlan, *, cancelling: bool = False) -> str:
    """The plan as a block somebody can check before agreeing to it."""
    verb = "Cancelling" if cancelling else f"Booking {plan.absence_type.label.lower()}"
    portion = "" if plan.portion is Portion.FULL else f" ({plan.portion.label.lower()})"
    lines = [f"{verb}{portion}"]

    for day in plan.days:
        if day.verdict is Verdict.BOOK:
            lines.append(f"  {short_date(day.date)}")
        elif day.verdict.is_skip:
            note = day.detail or VERDICT_NOTE.get(day.verdict, "skipped")
            lines.append(f"  {short_date(day.date)}   — {note}")
        else:
            lines.append(f"  {short_date(day.date)}   ✗ {day.reason}")

    if plan.is_empty:
        lines.append("")
        lines.append("Nothing to do.")
        return "\n".join(lines)

    booked = len(plan.bookable)
    lines.append("")
    plural = "" if booked == 1 else "s"
    lines.append(f"{booked} day{plural}, {fmt_days(plan.cost)} used")
    if plan.annual_after is not None and plan.absence_type.draws_down_entitlement:
        lines.append(
            f"Annual leave: {fmt_days(plan.annual_remaining or 0)}"
            f" → {fmt_days(plan.annual_after)} days left"
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
    head, portion, when = parse_request(words)

    try:
        start, end = parse_span(when or "today", today=today, prefer=Preference.FORWARD)
    except ValueError as error:
        raise click.UsageError(str(error)) from error

    if head == CANCEL_WORD:
        return _cancel(services, start, end, assume_yes=assume_yes, dry_run=dry_run)

    kind = absence_from_word(head)
    if kind is None:  # pragma: no cover - parse_request has already refused
        msg = f"'{head}' is not a kind of leave"
        raise click.UsageError(msg)

    if kind is AbsenceType.OTHER and not (note or "").strip():
        msg = "Other leave needs --note saying what it is"
        raise click.UsageError(msg)

    plan = services.absence.plan(
        start,
        end,
        kind,
        portion,
        note=note,
        available_toil_days=services.wallet.available_toil_days(today),
    )
    click.echo(render(plan))

    if plan.is_empty:
        return 1
    if dry_run:
        return 0
    if not assume_yes and not click.confirm("\nBook it?", default=False):
        click.echo("Nothing was booked.")
        return 1

    result = services.absence.book_plan(plan)
    click.secho(result.message("booked"), fg="green")
    return 0


def _cancel(
    services: Services,
    start: date,
    end: date,
    *,
    assume_yes: bool,
    dry_run: bool,
) -> int:
    booked = services.absence.in_range(start, end)
    if not booked:
        span = (
            long_date(start)
            if start == end
            else f"{short_date(start)} to {long_date(end)}"
        )
        click.echo(f"Nothing is booked on {span}.")
        return 1

    click.echo("Cancelling")
    for absence in booked:
        portion = (
            ""
            if absence.portion is Portion.FULL
            else f" ({absence.portion.label.lower()})"
        )
        click.echo(
            f"  {short_date(absence.date)}   {absence.absence_type.label}{portion}"
        )

    if dry_run:
        return 0
    if not assume_yes and not click.confirm("\nCancel these?", default=False):
        click.echo("Nothing was cancelled.")
        return 1

    result = services.absence.clear_range(start, end)
    click.secho(result.message("cancelled"), fg="green")
    return 0
