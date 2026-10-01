"""Compose the service graph once and expose it as an immutable value.

Which service depends on which is written down here and nowhere else, so a
widget never constructs its own and never reaches for the session behind it.

Built once. Nothing here caches a settings value, so a second registry buys
nothing, and a screen mounted before it would go on reading the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.domain.format import long_date
from flexi.services.absence import AbsenceService
from flexi.services.adjustments import (
    SETTLED,
    AdjustmentResult,
    AdjustmentService,
)
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.clock import ClockService
from flexi.services.ledger import LedgerService
from flexi.services.settings import SettingsService
from flexi.services.transactions import (
    WriteTransaction,
    bind_write_transaction,
)
from flexi.services.wallet import WalletService

__all__ = (
    "Services",
    "adjust_balance",
    "adjustment_refusal",
    "available_toil_days",
    "build_services",
    "invalidate_services",
    "minimum_session",
    "settlement_date",
    "zero_balance",
)


@dataclass(frozen=True, slots=True)
class Services:
    """The application services, wired together around one persistence scope.

    The SQLAlchemy session is an implementation detail of those services and no
    part of this bundle. :func:`build_services` assembles one.
    """

    settings: SettingsService
    bank_holidays: BankHolidayService
    clock: ClockService
    absence: AbsenceService
    adjustments: AdjustmentService
    ledger: LedgerService
    wallet: WalletService
    write: WriteTransaction


def build_services(session: Session) -> Services:
    """Construct the complete service graph in dependency order."""
    settings = SettingsService(session)
    bank_holidays = BankHolidayService(session, settings.get_division)
    absence = AbsenceService(session, settings, bank_holidays)
    ledger = LedgerService(session, settings, bank_holidays)
    return Services(
        settings=settings,
        bank_holidays=bank_holidays,
        clock=ClockService(session, settings, bank_holidays, minimum_session()),
        absence=absence,
        adjustments=AdjustmentService(session),
        ledger=ledger,
        wallet=WalletService(settings, absence, ledger),
        write=bind_write_transaction(session),
    )


def invalidate_services(services: Services) -> None:
    """Drop every cached derivation owned by the service graph."""
    services.ledger.invalidate()


def available_toil_days(services: Services, today: date | None = None) -> float:
    """The flexi balance in days: what a TOIL booking draws against."""
    return services.wallet.available_toil_days(today)


def settlement_date(as_of: date | None = None) -> date:
    """Return the date a settlement draws its line under.

    Yesterday, not today, when the caller does not say. Today is not over, and
    absorbing its contracted hours before they are worked leaves the evening
    looking like unearned overtime and tomorrow's balance wrong by a day.

    Public, because the command line names the date in the question it asks
    before it settles, and question and write resolve the default alike.
    """
    return as_of or wallclock.today() - timedelta(days=1)


def zero_balance(
    services: Services,
    as_of: date | None = None,
    *,
    reason: str = SETTLED,
) -> AdjustmentResult:
    """Settle the balance so that it reads zero as at the end of ``as_of``.

    A date that has not finished is refused. The balance is derived from a
    projection in which every day between now and then was worked zero hours,
    so the correction absorbs hours not yet worked, and the row stays invisible
    (`LedgerService._adjustments` filters on `date <= end`) until its date
    arrives, when the week's real hours read as pure surplus.

    A date earlier than a line already drawn in the same leave year is refused
    too: the correction is sized from the balance up to ``as_of``, which cannot
    see the later row, so the period they share is absorbed twice. A previous
    leave year is fair game, since each accumulates from its own start.

    Here, not in `flexi/cli/balance.py`, so the TUI and any embedder hold the
    same line.
    """
    as_of = settlement_date(as_of)
    if as_of >= wallclock.today():
        return AdjustmentResult(
            False,
            f"{long_date(as_of)} has not finished; settle to yesterday or before",
        )
    with services.write():
        # A preview may have memoised this period before another process wrote
        # to it. The writer reservation must come first; only then is a fresh
        # derivation stable until its compensating row is committed.
        services.ledger.invalidate()
        _, year_end = services.absence.leave_year_bounds(as_of)
        standing_line = services.adjustments.first_after(as_of, year_end)
        if standing_line is not None:
            return AdjustmentResult(
                False,
                f"A line was already drawn at {long_date(standing_line.date)};"
                f" undo it with `flexi balance undo {standing_line.id}`"
                " or settle on or after that date",
            )
        standing = services.ledger.balance(as_of).delta
        if not round(standing.total_seconds() / 60):
            return AdjustmentResult(False, "The balance is already zero")
        return services.adjustments.stage_record(as_of, -standing, reason)


def adjustment_refusal(services: Services, when: date) -> str | None:
    """Return why an adjustment cannot be dated ``when``, or None if it can.

    It has to fall in the current leave year: the balance starts again at each
    one, so a row dated in an earlier year is listed and never counted. It
    cannot be in the future, where the ledger hides it until the day arrives.

    And it has to come after the latest row dated before today. That row may be
    a settlement, which zeroes the balance up to its own date, and a correction
    dated on or before it reopens the period it closed. A reason is free text,
    so a settlement cannot be told from anything else; a row dated today never
    is one, because only a finished day can be settled.

    Public, because the command line checks before it shows the plan it asks
    about, and `adjust_balance` checks again under the writer reservation.
    """
    today = wallclock.today()
    if when > today:
        return f"{long_date(when)} has not happened; date it today or earlier"
    start, _ = services.absence.leave_year_bounds(today)
    if when < start:
        return (
            f"The balance starts again on {long_date(start)}, so an adjustment"
            f" dated {long_date(when)} would never count; date it on or after"
            " that day"
        )
    line = services.adjustments.last_before(today)
    if line is not None and when <= line.date:
        return (
            f"The adjustment dated {long_date(line.date)} may have settled the"
            " balance, and one dated on or before it would reopen what it closed;"
            " date this one after that day, or undo that one with"
            f" `flexi balance undo {line.id}`"
        )
    return None


def adjust_balance(
    services: Services,
    amount: timedelta,
    reason: str,
    on: date | None = None,
) -> AdjustmentResult:
    """Move the balance by ``amount`` from ``on``, which defaults to today.

    For a balance brought in from elsewhere, or a correction to one. Not
    yesterday by default, as a settlement is: on the first day of a leave year,
    yesterday belongs to the year before, where the row would never count.

    Here, not in `flexi/cli/balance.py`, so the TUI and any embedder hold the
    same line.
    """
    when = on or wallclock.today()
    with services.write():
        refusal = adjustment_refusal(services, when)
        if refusal is not None:
            return AdjustmentResult(False, refusal)
        return services.adjustments.stage_record(when, amount, reason)


def minimum_session() -> timedelta:
    """How long a session has to last to count.

    A preference, so it comes from the config file, not the database.
    """
    from flexi.config import CONFIG

    return timedelta(seconds=CONFIG.defaults.minimum_session_seconds)
