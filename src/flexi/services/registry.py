"""One object holding every service, built once and hung on the app.

The v1 code had every widget reach for ``self.app._session`` behind a
``# type: ignore`` and construct its own services — four constructions per
rebuild, in five files, each of which had to know which services depend on which
others. This is that knowledge, written down once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from flexi.services.absence import AbsenceService
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
            clock=ClockService(session),
            absence=absence,
            ledger=ledger,
            wallet=WalletService(session, settings, absence, ledger),
        )

    def invalidate(self) -> None:
        """Drop every cached derivation. Called after anything writes."""
        self.ledger.invalidate()

    def toil_days(self, today: date | None = None) -> float:
        """The flexi balance in days — what a TOIL booking would draw against."""
        return self.wallet.available_toil_days(today)

    def now(self) -> datetime:
        """The current local moment, in one place so tests can patch one thing."""
        return datetime.now()


def _division(settings: SettingsService) -> str:
    """The configured bank-holiday division, or the default before setup runs."""
    stored = settings.get_settings()
    if stored is None or not stored.bank_holiday_division:
        return "england-and-wales"
    return stored.bank_holiday_division
