"""What an allowance holds, and whether spending it is comfortable."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from flexi.constants import AbsenceType
from flexi.domain.balance import BalanceSummary
from flexi.domain.wallet import PACE_TOLERANCE, Allowance, Pace, WalletData

CONTRACTED = timedelta(hours=7, minutes=24)
TODAY = date(2026, 6, 11)


def annual(*, used: float, pace: float | None, total: float | None = 25.0) -> Allowance:
    return Allowance(
        type=AbsenceType.ANNUAL, used=used, occurrences=1, total=total, pace=pace
    )


def wallet(*, contracted: timedelta, surplus: timedelta) -> WalletData:
    """A wallet holding a flexi surplus, against a contracted day."""
    balance = BalanceSummary(worked=surplus)
    return WalletData(
        leave_year=(date(2026, 1, 1), date(2026, 12, 31)),
        balance=balance,
        period=BalanceSummary(),
        contracted=contracted,
        allowances=(annual(used=5.0, pace=5.0),),
    )


def test_allowance_spent_evenly_is_on_track() -> None:
    assert annual(used=10.0, pace=10.0).pace_state is Pace.ON_TRACK


def test_allowance_behind_the_pace_is_on_track() -> None:
    assert annual(used=2.0, pace=10.0).pace_state is Pace.ON_TRACK


def test_slightly_ahead_is_still_on_track() -> None:
    """`PACE_TOLERANCE` of twenty-five days is a long weekend."""
    just_inside = 10.0 + 25.0 * PACE_TOLERANCE - 0.1
    assert annual(used=just_inside, pace=10.0).pace_state is Pace.ON_TRACK


def test_far_enough_ahead_is_worth_saying() -> None:
    just_outside = 10.0 + 25.0 * PACE_TOLERANCE + 0.1
    assert annual(used=just_outside, pace=10.0).pace_state is Pace.AHEAD


@pytest.mark.parametrize(
    ("pace", "total"),
    [(None, 25.0), (10.0, None), (10.0, 0.0)],
    ids=["no pace", "uncapped", "nothing to spend"],
)
def test_there_is_nothing_to_say_without_an_entitlement(
    pace: float | None, total: float | None
) -> None:
    """A fresh install has neither run out of leave nor kept to a pace."""
    assert annual(used=5.0, pace=pace, total=total).pace_state is Pace.UNKNOWN


def test_tolerance_means_the_same_on_any_entitlement() -> None:
    """The tolerance is measured against the entitlement, not against the year."""
    generous = Allowance(
        type=AbsenceType.ANNUAL, used=4.0, occurrences=1, total=40.0, pace=1.0
    )
    modest = Allowance(
        type=AbsenceType.ANNUAL, used=4.0, occurrences=1, total=10.0, pace=1.0
    )

    assert generous.pace_state is Pace.ON_TRACK
    assert modest.pace_state is Pace.AHEAD


def test_allowance_names_and_colours_itself_by_type() -> None:
    """A token that drifts from its type matches no ``#gauge-{token}`` and vanishes."""
    toil = Allowance(type=AbsenceType.FLEXI, used=1.0, occurrences=1)
    assert toil.label == "TOIL"
    assert f"#gauge-{toil.token}" == "#gauge-toil"


def test_capped_allowance_reports_what_is_left() -> None:
    """Days left, counting a half day as a half; the gauge is drawn from it."""
    booked = annual(used=7.5, pace=10.0)
    assert booked.remaining == 17.5
    assert booked.is_capped


def test_allowance_with_no_entitlement_has_none_left() -> None:
    """``None`` is not zero: ``0.0`` draws a full red gauge before setup."""
    fresh = annual(used=0.0, pace=None, total=None)
    assert fresh.remaining is None
    assert not fresh.is_capped


def test_toil_reports_what_has_been_earned() -> None:
    """TOIL is uncapped, so the figure is the flexi balance standing behind it."""
    toil = Allowance(type=AbsenceType.FLEXI, used=3.0, occurrences=3, balance_days=1.5)
    assert toil.remaining == 1.5
    assert not toil.is_capped


def test_flexi_balance_is_worth_contracted_days() -> None:
    data = wallet(contracted=CONTRACTED, surplus=CONTRACTED * 2)
    assert data.balance_days == 2.0
    assert data.allowance(AbsenceType.ANNUAL).total == 25.0


def test_balance_is_worth_no_days_without_a_contract() -> None:
    """Zero, not a division by zero: contracted hours are zero before setup."""
    data = wallet(contracted=timedelta(), surplus=timedelta(hours=8))
    assert data.balance_days == 0.0
