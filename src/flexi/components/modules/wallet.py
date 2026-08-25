"""Every allowance, what is left, and whether that is on track.

The gauge marks where an even spread through the leave year would have you,
which is the difference between a figure and a judgement.

Tone is decided here rather than in :class:`~flexi.components.common.Gauge`,
because whether an underspent allowance is good news is a question about leave
policy, not about bars.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.message import Message

from flexi.components.common import Gauge, Tone
from flexi.components.modules.base import Module
from flexi.constants import AbsenceType
from flexi.domain.format import days, delta, plural, signed_days
from flexi.domain.wallet import Allowance, Pace, WalletData
from flexi.messages import Scope

TRACKED: tuple[AbsenceType, ...] = (
    AbsenceType.ANNUAL,
    AbsenceType.FLEXI,
    AbsenceType.SICK,
    AbsenceType.UNPAID,
    AbsenceType.OTHER,
)


class BookRequested(Message):
    """A shifted key asked to book one type of absence.

    The screen owns the modal, because a modal has to be pushed onto a screen and
    because the booking needs the flexi balance, which the screen already has.
    """

    def __init__(self, kind: AbsenceType) -> None:
        super().__init__()
        self.kind = kind


class WalletModule(Module):
    """One gauge per allowance, plus the period's own figures."""

    HELP_LABEL = "Wallet"

    WATCHES: ClassVar[Scope] = (
        Scope.ABSENCE | Scope.CLOCK | Scope.SETTINGS | Scope.PERIOD
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(id="wallet-module", title="Wallet", **kwargs)

    def compose(self) -> ComposeResult:
        for kind in TRACKED:
            yield Gauge(kind.short, id=f"gauge-{kind.token}")

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        period = self.period
        data = self.services.wallet.compute(
            period.start, period.end, today=self.now.date(), now=self.now
        )
        for kind in TRACKED:
            self._draw(data.allowance(kind), data)
        self.set_subtitle(_period_subtitle(data))

    def _draw(self, allowance: Allowance, data: WalletData) -> None:
        gauge = self.query_one(f"#gauge-{allowance.token}", Gauge)
        if allowance.type.draws_down_balance:
            self._draw_toil(gauge, data)
            return
        if allowance.is_capped:
            self._draw_capped(gauge, allowance)
            return
        self._draw_counted(gauge, allowance)

    def _draw_toil(self, gauge: Gauge, data: WalletData) -> None:
        """TOIL has no entitlement — it has a balance, which can go negative.

        The track shows the balance against a nominal five days either way, so a
        surplus and a deficit are drawn at comparable scale rather than the bar
        rescaling itself every time the number moves.
        """
        balance_days = data.balance_days
        gauge.display = True
        gauge.show(
            max(0.0, min(balance_days, 5.0)),
            readout=(
                f"{delta(data.balance.delta)}  ({signed_days(round(balance_days, 1))}d)"
            ),
            total=5.0,
            tone=Tone.OK if balance_days >= 0 else Tone.ERR,
        )

    def _draw_capped(self, gauge: Gauge, allowance: Allowance) -> None:
        """An entitlement, drawn as spent against total with a pace marker.

        `remaining` cannot be `None` here: `_draw` sends anything that draws
        down the balance to `_draw_toil`, and anything uncapped to
        `_draw_counted`, so what reaches this method has a total and does not
        read its remainder off the flexi balance. A "no entitlement set" arm
        lived here for a state those two guards make unreachable.
        """
        total = allowance.total or 0.0
        gauge.display = True
        gauge.show(
            allowance.used,
            readout=f"{days(total - allowance.used)} left of {days(total)}",
            total=total,
            target=allowance.pace,
            tone=_pace_tone(allowance),
        )

    def _draw_counted(self, gauge: Gauge, allowance: Allowance) -> None:
        """An uncapped type: reported, never limited, so it gets a line, not a bar.

        A type with nothing recorded is hidden outright. Five allowances is four
        rows of "none" in a sidebar that also has to fit a calendar, and an empty
        track says nothing that an absent row does not say better.
        """
        if not allowance.used:
            gauge.display = False
            return
        gauge.display = True
        occasions = plural(allowance.occurrences, "occasion")
        gauge.show(
            None,
            readout=f"{days(allowance.used)}d · {allowance.occurrences} {occasions}",
            total=1.0,
            tone=Tone.NEUTRAL,
            compact=True,
        )


def _pace_tone(allowance: Allowance) -> Tone:
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


def _period_subtitle(data: WalletData) -> str:
    """The module's live slot: how the shown period is doing."""
    return f"{delta(data.period.delta)} this period"
