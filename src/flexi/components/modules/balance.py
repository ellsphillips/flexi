"""The flexi balance, drawn as the dashboard headline.

The only Textual ``Digits`` in the application: a terminal has one font at one
size, so scale has to be drawn.

Zero is drawn unsigned and muted, because ``+0:00`` reads as a small surplus.
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
        # Whole minutes, so these digits and `flexi balance show` agree.
        summary = services.ledger.balance(today, now=self.now).as_shown()
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
        """Return the caption: the balance in hours and again in days."""
        if is_level(value):
            return "Level with contracted hours"
        if not contracted:
            return delta(value)
        days = round(value / contracted, 1)
        word = "banked" if value > timedelta() else "owed"
        if not days:
            # Under a tenth of a day: "0 days" beside a non-zero figure
            # reads as a contradiction.
            return f"{hm(value)} {word}"
        return f"{hm(value)} {word} · {signed_days(days)} {plural(abs(days), 'day')}"


def lean_class(value: timedelta) -> str:
    """Return the state class for ``value``, by the rule that draws the digits.

    The test is `is_level`, not a comparison against zero, so the colour cannot
    claim a direction the digits do not show.
    """
    if is_level(value):
        return "muted"
    return "surplus" if value > timedelta() else "deficit"
