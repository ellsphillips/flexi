"""What an allowance holds, and whether that is comfortable.

The judgement lived in the dashboard widget as a bare constant and four lines of
arithmetic, with no test anywhere -- while the version that *was* tested,
`Allowance.ahead_of_pace`, had no caller in production. Two rules, one shipped
and one checked, and they were not the same rule: the widget measured overspend
against the entitlement and the tested one compared against the pace directly.
"""

from __future__ import annotations

import pytest

from flexi.constants import AbsenceType
from flexi.domain.wallet import PACE_TOLERANCE, Allowance, Pace


def annual(*, used: float, pace: float | None, total: float | None = 25.0) -> Allowance:
    return Allowance(
        type=AbsenceType.ANNUAL, used=used, occurrences=1, total=total, pace=pace
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
