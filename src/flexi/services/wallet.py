"""Assembling the wallet from the database.

The values it assembles -- `Allowance` and `WalletData`, and the judgement about
whether an allowance is running ahead -- live in `flexi.domain.wallet`, where
they can be read and tested without a session.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import AbsenceType
from flexi.domain import leaveyear
from flexi.domain.wallet import Allowance, WalletData
from flexi.services.absence import AbsenceService
from flexi.services.ledger import LedgerService
from flexi.services.settings import SettingsService


class WalletService:
    """Compute the wallet's view model."""

    def __init__(
        self,
        session: Session,
        settings: SettingsService,
        absence: AbsenceService,
        ledger: LedgerService,
    ) -> None:
        """A second, divergent copy of the wiring diagram used to live here.

        The `or` fallbacks had exactly one caller, which passed all three, so
        they were never taken -- but they built a `BankHolidayService` with the
        default division unconditionally and threw it away, and the ledger
        fallback would have created a second memo cache that
        `Services.invalidate` does not clear.
        """
        self._session = session
        self._settings = settings
        self._absence = absence
        self._ledger = ledger

    def compute(
        self,
        start: date,
        end: date,
        *,
        today: date | None = None,
        now: datetime | None = None,
    ) -> WalletData:
        """The wallet as at ``today``, with ``start``–``end`` as the shown period."""
        today = today or wallclock.today()
        year_start, year_end = self._absence.leave_year_bounds(today)
        elapsed = leaveyear.fraction_elapsed(year_start, year_end, today)
        contracted = self._settings.get_contracted()

        balance = self._ledger.balance(today, now=now)
        period = self._ledger.summary(start, end, now=now)
        balance_days = balance.delta / contracted if contracted else 0.0

        return WalletData(
            leave_year=(year_start, year_end),
            elapsed=elapsed,
            balance=balance,
            period=period,
            today=self._ledger.day(today, now=now),
            contracted=contracted,
            allowances=self._allowances(year_start, year_end, elapsed, balance_days),
        )

    def _allowances(
        self,
        year_start: date,
        year_end: date,
        elapsed: float,
        balance_days: float,
    ) -> tuple[Allowance, ...]:
        entitlement = self._settings.get_active_entitlement_days(year_start)
        # One pass over the year's rows. It used to ask for days and occurrences
        # separately, per type -- ten scans of the same rows with byte-identical
        # arguments, each re-validating every row it read with three queries of
        # its own, so a year of twenty-five bookings cost 162 round trips.
        counted = self._absence.tally(year_start, year_end)

        def allowance(kind: AbsenceType) -> Allowance:
            total = entitlement if kind.draws_down_entitlement else None
            return Allowance(
                type=kind,
                used=counted[kind].days,
                occurrences=counted[kind].occurrences,
                total=total,
                pace=None if total is None else total * elapsed,
                balance_days=balance_days if kind.draws_down_balance else None,
            )

        return tuple(allowance(kind) for kind in AbsenceType)

    # -- convenience for the absence modal ---------------------------------

    def available_toil_days(self, today: date | None = None) -> float:
        """How many days of TOIL could still be taken without going into deficit.

        The running balance only accumulates up to *today*, so TOIL already
        booked for next month is invisible to it. Subtracting those bookings is
        what stops the interface cheerfully accepting an unlimited number of
        future TOIL days and only mentioning the deficit once they arrive.
        """
        today = today or wallclock.today()
        contracted = self._settings.get_contracted()
        if not contracted:
            return 0.0
        banked = self._ledger.balance(today).delta / contracted
        _, year_end = self._absence.leave_year_bounds(today)
        committed = self._absence.count_days(
            AbsenceType.FLEXI, today + timedelta(days=1), year_end
        )
        return banked - committed
