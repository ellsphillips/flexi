"""What an allowance holds, and whether that is comfortable.

The judgement lived in the dashboard widget as a bare constant and four lines of
arithmetic, with no test anywhere -- while the version that *was* tested,
`Allowance.ahead_of_pace`, had no caller in production. Two rules, one shipped
and one checked, and they were not the same rule: the widget measured overspend
against the entitlement and the tested one compared against the pace directly.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from flexi.constants import AbsenceType, DayKind
from flexi.domain.balance import BalanceSummary
from flexi.domain.ledger import DayLedger
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
        elapsed=0.5,
        balance=balance,
        period=BalanceSummary(),
        today=DayLedger(
            date=TODAY,
            kind=DayKind.WORKING,
            is_working_day=True,
            contracted=contracted,
            worked=timedelta(),
            expected=contracted,
        ),
        contracted=contracted,
        allowances=(annual(used=5.0, pace=5.0),),
    )


def test_an_allowance_spent_evenly_is_on_track() -> None:
    assert annual(used=10.0, pace=10.0).pace_state is Pace.ON_TRACK


def test_an_allowance_spent_behind_the_pace_is_on_track() -> None:
    """Underspending is not a warning. It is most of a leave year."""
    assert annual(used=2.0, pace=10.0).pace_state is Pace.ON_TRACK


def test_a_little_ahead_is_still_on_track() -> None:
    """Fifteen per cent of twenty-five days is a long weekend.

    Flagging anything tighter would cry wolf every April, when school holidays
    land several days at once.
    """
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
    """`UNKNOWN` is not `ON_TRACK`, and the interface must not draw it as such.

    A fresh install with no entitlement recorded has not run out of leave and is
    not keeping to a pace either.
    """
    assert annual(used=5.0, pace=pace, total=total).pace_state is Pace.UNKNOWN


def test_the_tolerance_means_the_same_on_any_entitlement() -> None:
    """Days, not a proportion of the year so far.

    Measured against the entitlement, so the tolerance means the same thing on
    twenty-five days as on five.
    """
    generous = Allowance(
        type=AbsenceType.ANNUAL, used=4.0, occurrences=1, total=40.0, pace=1.0
    )
    modest = Allowance(
        type=AbsenceType.ANNUAL, used=4.0, occurrences=1, total=10.0, pace=1.0
    )

    assert generous.pace_state is Pace.ON_TRACK
    assert modest.pace_state is Pace.AHEAD


def test_an_allowance_names_and_colours_itself_from_its_type() -> None:
    """One place decides what TOIL is called and which colour it is drawn in.

    The wallet finds a row's gauge by querying ``#gauge-{token}``. A token that
    drifted from its type would not raise: the query would simply match nothing
    and the row would silently stop being drawn.
    """
    toil = Allowance(type=AbsenceType.FLEXI, used=1.0, occurrences=1)
    assert toil.label == "TOIL"
    assert f"#gauge-{toil.token}" == "#gauge-toil"


def test_a_capped_allowance_reports_what_is_left_of_the_entitlement() -> None:
    """Days left, counting a half day as a half.

    The figure the wallet puts in front of somebody deciding whether they can
    book next week, and the one the gauge is drawn from.
    """
    booked = annual(used=7.5, pace=10.0)
    assert booked.remaining == 17.5
    assert booked.is_capped


def test_an_allowance_with_no_entitlement_has_none_left_rather_than_zero() -> None:
    """``None`` is not zero, and the wallet must not draw it as zero.

    Nothing is recorded against annual leave until setup has been through, and
    the wallet is drawn on the way there. Falling through to ``0.0`` would meet
    somebody on their first morning with a full red gauge and no days left,
    which is the one reading that is both plausible and wrong.
    """
    fresh = annual(used=0.0, pace=None, total=None)
    assert fresh.remaining is None
    assert not fresh.is_capped


def test_toil_reports_what_has_been_earned_not_what_is_left_of_a_quota() -> None:
    """There is no TOIL entitlement to subtract from.

    Every other row answers "how much of the allowance is unspent"; TOIL is
    uncapped, and the only meaningful figure is the flexi balance standing
    behind it. Falling through to the entitlement arithmetic would report
    ``None`` — no allowance recorded — for somebody with days in hand.
    """
    toil = Allowance(type=AbsenceType.FLEXI, used=3.0, occurrences=3, balance_days=1.5)
    assert toil.remaining == 1.5
    assert not toil.is_capped


def test_a_flexi_balance_is_worth_days_of_the_contracted_day() -> None:
    """Hours mean nothing to a leave screen counting in days."""
    data = wallet(contracted=CONTRACTED, surplus=CONTRACTED * 2)
    assert data.balance_days == 2.0
    assert data.allowance(AbsenceType.ANNUAL).total == 25.0


def test_a_balance_is_worth_no_days_until_a_contracted_day_is_recorded() -> None:
    """Zero, not a division by zero.

    Contracted hours are zero until setup has been through, and the wallet is
    drawn on the way there. Whatever surplus the clock events add up to, there
    is no day length to divide it by, so there is no answer in days yet.
    """
    data = wallet(contracted=timedelta(), surplus=timedelta(hours=8))
    assert data.balance_days == 0.0
