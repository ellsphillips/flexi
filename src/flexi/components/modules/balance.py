"""The balance: the one number the application exists to show.

This is the only place in Flexi where type gets bigger. A terminal has one font
at one size, so scale has to be *drawn*, and Textual's ``Digits`` draws a 3×3
glyph per character. Spending that effect on exactly one figure is what makes it
read as the headline rather than as one stat among five.

The sign is mandatory and coloured — surplus green, deficit red — and zero is
drawn unsigned and muted, because ``+0:00`` reads as a small surplus and the
point of the figure is that there is not one.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.widgets import Digits, Static

from flexi.components.modules.base import Module
from flexi.domain.format import delta, digits, hm, signed_days
from flexi.messages import Scope

STATE_CLASSES = ("surplus", "deficit", "muted")


class BalanceModule(Module):
    """Flexi hours banked or owed, for the leave year to date."""

    WATCHES: ClassVar[Scope] = Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(id="balance-module", title="Balance", **kwargs)

    def compose(self) -> ComposeResult:
        yield Digits("0:00", id="balance-digits")
        yield Static("FLEXI BALANCE", classes="overline", id="balance-label")
        yield Static("", classes="caption", id="balance-detail")

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        services = self.services
        today = self.now.date()
        summary = services.ledger.balance(today, now=self.now)
        contracted = services.settings.get_contracted()

        readout = self.query_one("#balance-digits", Digits)
        readout.update(digits(summary.delta))
        readout.remove_class(*STATE_CLASSES)
        readout.add_class(_state_class(summary.delta))

        self.query_one("#balance-detail", Static).update(
            self._detail(summary.delta, contracted)
        )
        start, end = services.absence.leave_year_bounds(today)
        self.set_subtitle(f"{start.strftime('%-d %b %y')}–{end.strftime('%-d %b %y')}")

    def _detail(self, value: timedelta, contracted: timedelta) -> str:
        """The caption: the same figure said a second way.

        Hours are what the balance is measured in; days are what it is spent in.
        Showing both removes the arithmetic a reader would otherwise do in their
        head before deciding whether they can take Friday off.
        """
        if not value:
            return "Level with contracted hours"
        if not contracted:
            return delta(value)
        days = value / contracted
        word = "banked" if value > timedelta() else "owed"
        return f"{hm(value)} {word} · {signed_days(round(days, 1))} days"


def _state_class(value: timedelta) -> str:
    if value > timedelta():
        return "surplus"
    if value < timedelta():
        return "deficit"
    return "muted"
