"""How one allowance is drawn on a gauge.

Two places draw the wallet -- the dashboard's module and the leave planner's
sidebar -- and they had drifted. The planner passed a hardcoded ``Tone.OK``, so
on the one screen where the question actually is *can I afford this booking*, an
exhausted allowance was drawn in the same green as a healthy one and an
overspend never went amber. It also drew a zero entitlement against a nominal
total of one, filling the bar for somebody who had been given no leave at all,
and carried an arm for a capped allowance with no remainder -- a state
:attr:`~flexi.domain.wallet.Allowance.is_capped` already rules out.

*Which* allowances a screen shows remains the screen's business: the sidebar has
room for three where the dashboard has five. How one of them looks is not.

Tone is decided here rather than in :class:`~flexi.components.common.Gauge`,
because whether an underspent allowance is good news is a question about leave
policy, not about bars.
"""

from __future__ import annotations

from flexi.components.common import Gauge, Tone
from flexi.domain.format import days, delta, plural, signed_days
from flexi.domain.wallet import Allowance, Pace, WalletData

__all__ = (
    "TOIL_SCALE",
    "pace_tone",
    "paint_allowance",
    "paint_balance",
    "paint_entitlement",
    "paint_tally",
)

TOIL_SCALE = 5.0
"""Days either side of zero the TOIL track spans.

A nominal scale, so that a surplus and a deficit are drawn at comparable size
rather than the bar rescaling itself every time the number moves.
"""


def paint_allowance(gauge: Gauge, allowance: Allowance, data: WalletData) -> None:
    """Draw one allowance, in whichever of the three readings its type calls for."""
    if allowance.type.draws_down_balance:
        paint_balance(gauge, data)
    elif allowance.is_capped:
        paint_entitlement(gauge, allowance)
    else:
        paint_tally(gauge, allowance)


def paint_balance(gauge: Gauge, data: WalletData) -> None:
    """TOIL has no entitlement -- it has a balance, which can go negative."""
    balance_days = data.balance_days
    gauge.display = True
    gauge.show(
        max(0.0, min(balance_days, TOIL_SCALE)),
        readout=(
            f"{delta(data.balance.delta)}  ({signed_days(round(balance_days, 1))}d)"
        ),
        total=TOIL_SCALE,
        tone=Tone.OK if balance_days >= 0 else Tone.ERR,
    )


def paint_entitlement(gauge: Gauge, allowance: Allowance) -> None:
    """An entitlement, drawn as spent against total with a pace marker.

    ``remaining`` cannot be ``None`` here: :func:`paint_allowance` sends anything
    that draws down the balance to :func:`paint_balance` and anything uncapped to
    :func:`paint_tally`, so what reaches this function has a total and does not
    read its remainder off the flexi balance.
    """
    total = allowance.total or 0.0
    gauge.display = True
    gauge.show(
        allowance.used,
        readout=f"{days(total - allowance.used)} left of {days(total)}",
        total=total,
        target=allowance.pace,
        tone=pace_tone(allowance),
    )


def paint_tally(gauge: Gauge, allowance: Allowance) -> None:
    """An uncapped type: reported, never limited, so it gets a line, not a bar.

    A type with nothing recorded is hidden outright. Five allowances is four rows
    of "none" in a sidebar that also has to fit a calendar, and an empty track
    says nothing that an absent row does not say better.
    """
    if not allowance.used:
        gauge.display = False
        return
    gauge.display = True
    gauge.show(
        None,
        readout=(
            f"{days(allowance.used)}d · {allowance.occurrences} "
            f"{plural(allowance.occurrences, 'occasion')}"
        ),
        total=1.0,
        tone=Tone.NEUTRAL,
        compact=True,
    )


def pace_tone(allowance: Allowance) -> Tone:
    """Amber when an entitlement is being spent faster than the year is passing.

    Not red: spending leave early is a plan, not a fault. Red is reserved for
    having none left, which is the only state that stops you booking.
    """
    if allowance.remaining is not None and allowance.remaining <= 0:
        return Tone.ERR
    return {
        Pace.UNKNOWN: Tone.NEUTRAL,
        Pace.ON_TRACK: Tone.OK,
        Pace.AHEAD: Tone.WARN,
    }[allowance.pace_state]
