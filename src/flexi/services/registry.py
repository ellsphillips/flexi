"""One object holding every service, built once and hung on the app.

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
        bank_holidays = BankHolidayService(session, settings.get_division)
        absence = AbsenceService(session, settings, bank_holidays)
        ledger = LedgerService(session, settings, bank_holidays)
        return cls(
            session=session,
            settings=settings,
            bank_holidays=bank_holidays,
            clock=ClockService(session, settings, bank_holidays, minimum_session()),
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

    @staticmethod
    def settles_to(as_of: date | None = None) -> date:
        """The date a settlement draws its line under.

        *Yesterday*, not today, when the caller does not say. Today is not
        over: absorbing its contracted hours before they have been worked would
        leave the evening looking like unearned overtime, and tomorrow's
        balance wrong by a day. Settling to the end of yesterday leaves today
        behaving exactly as any other day does.

        Public, because the command line has to name the date in the question
        it asks before it settles -- and it resolved the default a second time
        to do so, six lines from a third statement of the same rule in a Click
        help string.
        """
        return as_of or wallclock.today() - timedelta(days=1)

    def zero_balance(
        self, as_of: date | None = None, *, reason: str = OPENING_BALANCE
    ) -> AdjustmentResult:
        """Settle the balance so that it reads zero as at the end of ``as_of``."""
        as_of = self.settles_to(as_of)
        standing = self.ledger.balance(as_of).delta
        if not round(standing.total_seconds() / 60):
            return AdjustmentResult(False, "The balance is already zero")
        result = self.adjustments.record(as_of, -standing, reason)
        if result.success:
            self.invalidate()
        return result


def minimum_session() -> timedelta:
    """How long a session has to last to count.

    A preference, so it comes from the config file rather than the database.
    """
    from flexi.config import CONFIG

    return timedelta(seconds=CONFIG.defaults.minimum_session_seconds)
