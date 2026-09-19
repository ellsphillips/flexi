"""How one allowance is drawn on a gauge.

Which allowances a screen shows is the screen's business; how one of them looks
is settled here, so the dashboard module and the leave sidebar draw a wallet
the same way.

Tone is decided here and not in :class:`~flexi.components.common.Gauge`,
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

A nominal scale, fixed so that a surplus and a deficit are drawn at comparable
size and the bar does not rescale every time the number moves.
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
    """TOIL has no entitlement, only a balance, which can go negative."""
    balance_days = data.balance_days
    rounded = round(balance_days, 1)
    hours = delta(data.balance.delta)
    gauge.display = True
    gauge.show(
        max(0.0, min(balance_days, TOIL_SCALE)),
        # Under a tenth of a day the hours stand alone: "(0d)" beside a figure
        # that is not zero reads as a contradiction.
        readout=f"{hours}  ({signed_days(rounded)}d)" if rounded else hours,
        total=TOIL_SCALE,
        tone=Tone.OK if balance_days >= 0 else Tone.ERR,
    )


def paint_entitlement(gauge: Gauge, allowance: Allowance) -> None:
    """An entitlement, drawn as spent against total with a pace marker.

    ``remaining`` cannot be ``None`` here: :func:`paint_allowance` sends
    anything that draws down the balance to :func:`paint_balance` and anything
    uncapped to :func:`paint_tally`, so only capped entitlements arrive.
    """
    total = allowance.total or 0.0
    left = total - allowance.used
    # An entitlement lowered below what is already booked leaves a negative
    # remainder, and `signed_days` writes its minus with U+2212 like the rest.
    remaining = signed_days(left) if left < 0 else days(left)
    gauge.display = True
    gauge.show(
        allowance.used,
        readout=f"{remaining} left of {days(total)}",
        total=total,
        target=allowance.pace,
        tone=pace_tone(allowance),
    )


def paint_tally(gauge: Gauge, allowance: Allowance) -> None:
    """An uncapped type: reported, never limited, so it gets a line, not a bar.

    A type with nothing recorded is hidden outright.
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

    Red is reserved for an exhausted entitlement, the state that stops a
    booking.
    """
    if allowance.remaining is not None and allowance.remaining <= 0:
        return Tone.ERR
    return {
        Pace.UNKNOWN: Tone.NEUTRAL,
        Pace.ON_TRACK: Tone.OK,
        Pace.AHEAD: Tone.WARN,
    }[allowance.pace_state]
