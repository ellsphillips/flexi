"""What is left in each allowance, and what the balance is doing.

Every allowance carries a pace -- where the figure would be if it were being
spent evenly -- because "18.5 days" is comfortable or alarming depending
entirely on how much of the leave year is left.

Numbers only. Whether an underspent allowance is good news is a question about
leave policy, and it is answered in the wallet module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import AbsenceType
from flexi.domain.balance import BalanceSummary
from flexi.domain.ledger import DayLedger
from flexi.services.absence import AbsenceService
from flexi.services.ledger import LedgerService
from flexi.services.settings import SettingsService


@dataclass(frozen=True, slots=True)
class Allowance:
    """One line of the wallet."""

    type: AbsenceType
    used: float
    """Days spent, counting a half as a half."""
    occurrences: int
    """How many separate bookings — two half-days are two occasions, one day."""
    total: float | None = None
    """The entitlement, where there is one. ``None`` means uncapped."""
    pace: float | None = None
    """Where ``used`` would be if the allowance were spent evenly over the year."""
    balance_days: float | None = None
    """For TOIL: days of flexi balance available rather than an entitlement."""

    @property
    def label(self) -> str:
        """The name shown to a reader."""
        return self.type.label

    @property
    def token(self) -> str:
        """The stem of this allowance's colour tokens."""
        return self.type.token

    @property
    def remaining(self) -> float | None:
        """Days left, or ``None`` when nothing has been recorded to draw against.

        ``None`` is not zero, and the interface must not draw it as zero: a fresh
        install with no entitlement recorded has not run out of leave.
        """
        if self.type.draws_down_balance:
            return self.balance_days
        if self.total is None:
            return None
        return self.total - self.used

    @property
    def is_capped(self) -> bool:
        """True when there is an entitlement to run out of."""
        return self.total is not None

    @property
    def ahead_of_pace(self) -> bool | None:
        """True when more has been spent than an even spread would have spent."""
        if self.pace is None:
            return None
        return self.used > self.pace


@dataclass(frozen=True, slots=True)
class WalletData:
    """Everything the wallet module draws."""

    leave_year: tuple[date, date]
    elapsed: float
    """How far through the leave year today is, 0.0 to 1.0."""
    balance: BalanceSummary
    """The running flexi balance, leave-year to date."""
    period: BalanceSummary
    """The same figures for the period currently on screen."""
    today: DayLedger
    contracted: timedelta
    allowances: tuple[Allowance, ...]

    @property
    def balance_days(self) -> float:
        """The flexi balance expressed in working days."""
        if not self.contracted:
            return 0.0
        return self.balance.delta / self.contracted

    def allowance(self, kind: AbsenceType) -> Allowance:
        """One allowance by type."""
        return next(item for item in self.allowances if item.type is kind)


class WalletService:
    """Compute the wallet's view model."""

    def __init__(
        self,
        session: Session,
        settings: SettingsService | None = None,
        absence: AbsenceService | None = None,
        ledger: LedgerService | None = None,
    ) -> None:
        from flexi.services.bank_holidays import BankHolidayService

        self._session = session
        self._settings = settings or SettingsService(session)
        holidays = BankHolidayService(session)
        self._absence = absence or AbsenceService(session, self._settings, holidays)
        self._ledger = ledger or LedgerService(session, self._settings)

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
        elapsed = _fraction_elapsed(year_start, year_end, today)
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
        allowances: list[Allowance] = []
        for kind in AbsenceType:
            used = self._absence.count_days(
                kind, start=year_start, end=year_end, valid_only=True
            )
            occurrences = self._absence.count_absences(
                kind, start=year_start, end=year_end, valid_only=True
            )
            total = entitlement if kind.draws_down_entitlement else None
            allowances.append(
                Allowance(
                    type=kind,
                    used=used,
                    occurrences=occurrences,
                    total=total,
                    pace=None if total is None else total * elapsed,
                    balance_days=balance_days if kind.draws_down_balance else None,
                )
            )
        return tuple(allowances)

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
            AbsenceType.FLEXI, start=today + timedelta(days=1), end=year_end
        )
        return banked - committed


def _fraction_elapsed(start: date, end: date, today: date) -> float:
    """How far through a span today is, clamped to 0..1.

    Clamped rather than allowed to run past 1.0 so a pace marker can never leave
    the track — a marker off the end of a gauge reads as a rendering fault, and
    the honest statement at that point is "all of it".
    """
    span = (end - start).days
    if span <= 0:
        return 1.0
    return min(1.0, max(0.0, (today - start).days / span))
