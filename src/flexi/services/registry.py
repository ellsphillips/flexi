"""One object holding every service, built once and hung on the app.

The v1 code had every widget reach for ``self.app._session`` behind a
``# type: ignore`` and construct its own services — four constructions per
rebuild, in five files, each of which had to know which services depend on which
others. This is that knowledge, written down once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

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


@dataclass(slots=True)
class Services:
    """Every service, wired together, sharing one session."""

    session: Session
    settings: SettingsService
    bank_holidays: BankHolidayService
    clock: ClockService
    absence: AbsenceService
    adjustments: AdjustmentService
    ledger: LedgerService
    wallet: WalletService

    @classmethod
    def build(cls, session: Session) -> Services:
        """Construct the whole graph in dependency order."""
        settings = SettingsService(session)
        division = _division(settings)
        bank_holidays = BankHolidayService(session, division)
        absence = AbsenceService(session, settings, bank_holidays)
        ledger = LedgerService(session, settings, division)
        return cls(
            session=session,
            settings=settings,
            bank_holidays=bank_holidays,
            clock=ClockService(session, _minimum_session()),
            absence=absence,
            adjustments=AdjustmentService(session),
            ledger=ledger,
            wallet=WalletService(session, settings, absence, ledger),
        )

    def invalidate(self) -> None:
        """Drop every cached derivation. Called after anything writes."""
        self.ledger.invalidate()

    def toil_days(self, today: date | None = None) -> float:
        """The flexi balance in days — what a TOIL booking would draw against."""
        return self.wallet.available_toil_days(today)

    def zero_balance(
        self, as_of: date | None = None, *, reason: str = OPENING_BALANCE
    ) -> AdjustmentResult:
        """Settle the balance so that it reads zero as at the end of ``as_of``.

        Defaults to *yesterday*, not today. Today is not over: absorbing its
        contracted hours before they have been worked would leave the evening
        looking like unearned overtime, and tomorrow's balance wrong by a day.
        Settling to the end of yesterday leaves today behaving exactly as any
        other day does.
        """
        as_of = as_of or (date.today() - timedelta(days=1))
        standing = self.ledger.balance(as_of).delta
        if not round(standing.total_seconds() / 60):
            return AdjustmentResult(False, "The balance is already zero")
        result = self.adjustments.record(as_of, -standing, reason)
        if result.success:
            self.invalidate()
        return result

    def now(self) -> datetime:
        """The current local moment, in one place so tests can patch one thing."""
        return datetime.now()


def _minimum_session() -> timedelta:
    """How long a session has to last to count. Preference, so it comes from
    the config file rather than from the database.
    """
    from flexi.config import CONFIG

    return timedelta(seconds=CONFIG.defaults.minimum_session_seconds)


def _division(settings: SettingsService) -> str:
    """The configured bank-holiday division, or the default before setup runs."""
    stored = settings.get_settings()
    if stored is None or not stored.bank_holiday_division:
        return "england-and-wales"
    return stored.bank_holiday_division
