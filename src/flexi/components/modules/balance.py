"""The one number the application exists to show.

The only place in Flexi where type gets bigger. A terminal has one font at one
size, so scale has to be drawn, and spending Textual's ``Digits`` on exactly one
figure is what makes it read as the headline.

Zero is drawn unsigned and muted, because ``+0:00`` reads as a small surplus and
the point of the figure is that there is not one.
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar, Unpack

from textual.app import ComposeResult
from textual.widgets import Digits, Static

from flexi.components.modules.base import Module
from flexi.components.options import ModuleOptions
from flexi.domain.format import (
    delta,
    digits,
    hm,
    is_level,
    plural,
    signed_days,
    stamp,
)
from flexi.messages import Scope

__all__ = ("STATE_CLASSES", "BalanceModule", "lean_class")

STATE_CLASSES = ("surplus", "deficit", "muted")


class BalanceModule(Module):
    """Flexi hours banked or owed, for the leave year to date."""

    WATCHES: ClassVar[Scope] = Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS

    def __init__(self, **kwargs: Unpack[ModuleOptions]) -> None:
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
        readout.add_class(lean_class(summary.delta))

        self.query_one("#balance-detail", Static).update(
            self._detail(summary.delta, contracted)
        )
        start, end = services.absence.leave_year_bounds(today)
        self.set_subtitle(f"{stamp(start, '%-d %b %y')}–{stamp(end, '%-d %b %y')}")

    def _detail(self, value: timedelta, contracted: timedelta) -> str:
        """The caption: the same figure said a second way.

        Hours are what the balance is measured in; days are what it is spent in.
        Showing both removes the arithmetic a reader would otherwise do in their
        head before deciding whether they can take Friday off.
        """
        if is_level(value):
            return "Level with contracted hours"
        if not contracted:
            return delta(value)
        days = round(value / contracted, 1)
        word = "banked" if value > timedelta() else "owed"
        return f"{hm(value)} {word} · {signed_days(days)} {plural(abs(days), 'day')}"


def lean_class(value: timedelta) -> str:
    """Which way the figure beside it leans, by the same rule that draws it.

    Through `is_level` rather than against zero, so the colour cannot claim a
    direction the digits do not show: forty seconds of deficit drew `0:00` in
    red before the two agreed on what counts as level.
    """
    if is_level(value):
        return "muted"
    return "surplus" if value > timedelta() else "deficit"
