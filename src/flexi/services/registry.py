"""Compose the service graph once and expose it as an immutable value.

Which service depends on which is written down here and nowhere else, so a
widget never constructs its own and never reaches for the session behind it.

Built *once*, and that matters. Nothing here caches a settings value, so there
is never a reason to build a second one -- and the moment there were two, a
screen pushed before the rebuild went on reading the registry the rebuild had
replaced, while the modules inside that same screen read the new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.services.absence import AbsenceService
from flexi.services.adjustments import (
    OPENING_BALANCE,
    AdjustmentResult,
    AdjustmentService,
)
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.clock import ClockService
from flexi.services.ledger import LedgerService
from flexi.services.settings import SettingsService
from flexi.services.wallet import WalletService

__all__ = (
    "Services",
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

    The SQLAlchemy session is an implementation detail of those services, not
    part of this public bundle. Construction is the free :func:`build_services`
    function because building a value is not behavior of the value itself.
    """

    settings: SettingsService
    bank_holidays: BankHolidayService
    clock: ClockService
    absence: AbsenceService
    adjustments: AdjustmentService
    ledger: LedgerService
    wallet: WalletService


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
    )


def invalidate_services(services: Services) -> None:
    """Drop every cached derivation owned by the service graph."""
    services.ledger.invalidate()


def available_toil_days(services: Services, today: date | None = None) -> float:
    """The flexi balance in days — what a TOIL booking would draw against."""
    return services.wallet.available_toil_days(today)


def settlement_date(as_of: date | None = None) -> date:
    """The date a settlement draws its line under.

    *Yesterday*, not today, when the caller does not say. Today is not over:
    absorbing its contracted hours before they have been worked would leave the
    evening looking like unearned overtime, and tomorrow's balance wrong by a
    day. Settling to the end of yesterday leaves today behaving exactly as any
    other day does.

    Public, because the command line has to name the date in the question it
    asks before it settles, and both the question and the write must resolve the
    default by the same rule.
    """
    return as_of or wallclock.today() - timedelta(days=1)


def zero_balance(
    services: Services,
    as_of: date | None = None,
    *,
    reason: str = OPENING_BALANCE,
) -> AdjustmentResult:
    """Settle the balance so that it reads zero as at the end of ``as_of``."""
    as_of = settlement_date(as_of)
    standing = services.ledger.balance(as_of).delta
    if not round(standing.total_seconds() / 60):
        return AdjustmentResult(False, "The balance is already zero")
    result = services.adjustments.record(as_of, -standing, reason)
    if result.success:
        invalidate_services(services)
    return result


def minimum_session() -> timedelta:
    """How long a session has to last to count.

    A preference, so it comes from the config file rather than the database.
    """
    from flexi.config import CONFIG

    return timedelta(seconds=CONFIG.defaults.minimum_session_seconds)
