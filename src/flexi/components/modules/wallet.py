"""Every allowance, what is left, and whether that is on track.

The gauge marks where an even spread through the leave year would have you,
which is the difference between a figure and a judgement. How a single gauge is
painted lives in :mod:`flexi.components.allowance`, because the leave planner
draws the same wallet in its sidebar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.message import Message

from flexi.components.allowance import paint_allowance
from flexi.components.common import Gauge
from flexi.components.modules.base import Module
from flexi.constants import AbsenceType
from flexi.domain.format import delta
from flexi.messages import Scope

__all__ = ("TRACKED", "BookRequested", "WalletModule")

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
            allowance = data.allowance(kind)
            paint_allowance(
                self.query_one(f"#gauge-{allowance.token}", Gauge), allowance, data
            )
        self.set_subtitle(f"{delta(data.period.delta)} this period")
