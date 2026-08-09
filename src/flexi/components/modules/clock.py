"""Are you on the clock, since when, and when can you go.

One key does the whole thing. The switch and the button exist so a pointer works
and so a visible control teaches the key beside it.

The subtitle carries the elapsed time while a session is open and updates every
second: a minute-grained readout jumping in sixty-second steps looks like a hung
process.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Static, Switch

from flexi.components.common import Pill, Tone
from flexi.components.modules.base import Module
from flexi.components.punch import PunchStrip
from flexi.domain.format import clock, hm, hms
from flexi.domain.ledger import DayLedger
from flexi.messages import Scope


class ClockModule(Module):
    """Clock in, clock out, and see today at a glance."""

    WATCHES: ClassVar[Scope] = Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(id="clock-module", title="Clock", subtitle="/", **kwargs)
        self._ledger: DayLedger | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="clock-state"):
            yield Pill("off the clock", id="clock-pill")
            yield Switch(value=False, id="clock-switch")
        yield PunchStrip(id="clock-strip")
        yield Static("", id="clock-detail", classes="caption")
        yield Button("Arrive", id="clock-button", classes="-primary")

    def on_mount(self) -> None:
        self.rebuild()

    # -- drawing -----------------------------------------------------------

    def rebuild(self) -> None:
        services = self.services
        today = self.now.date()
        self._ledger = services.ledger.day(today, now=self.now)
        ledger = self._ledger
        on_clock = ledger.is_open

        self.query_one("#clock-pill", Pill).set_state(
            "on the clock" if on_clock else "off the clock",
            Tone.ACCENT if on_clock else Tone.NEUTRAL,
        )

        # Plain assignment, so the slider animates. `set_reactive` writes the
        # value without running the watcher, and the watcher is the animation —
        # pressing `/` moved the clock and left the switch sitting still.
        #
        # The Changed event this posts is safe: `_ledger` was refreshed from the
        # database a few lines above, so `on_switch_changed` sees the switch and
        # the truth already agreeing and does nothing.
        self.query_one("#clock-switch", Switch).value = on_clock

        button = self.query_one("#clock-button", Button)
        button.label = "Depart" if on_clock else "Arrive"

        self.query_one("#clock-strip", PunchStrip).set_ledger(
            ledger, window=services.ledger.window, now=self.now
        )
        self.query_one("#clock-detail", Static).update(self._detail(ledger))
        self.tick()

    def tick(self) -> None:
        """Refresh only what changes second to second."""
        if self._ledger is None or not self._ledger.is_open:
            self.set_subtitle("/")
            return
        elapsed = self._ledger.worked
        self.set_subtitle(hms(elapsed))

    def _detail(self, ledger: DayLedger) -> str:
        """The one line under the strip: where today stands."""
        if ledger.is_holiday:
            return ledger.holiday_title or "Bank holiday"
        if ledger.absences and not ledger.segments:
            return ledger.summary
        first = ledger.first_in
        if first is None:
            return "Not arrived" if ledger.is_working_day else "Not a working day"
        parts = [f"since {clock(first)}"]
        leave_at = ledger.leave_at()
        if leave_at is not None and ledger.is_open:
            parts.append(f"go home {clock(leave_at)}")
        else:
            parts.append(f"worked {hm(ledger.worked)}")
        # Break time is deliberately not here. The line has thirty columns and
        # loses its last word at thirty-one; the breakdown is one `space` away
        # in the records table, where there is room to say it properly.
        return " · ".join(parts)

    # -- interaction -------------------------------------------------------

    class Toggle(Message):
        """The user asked to clock in or out. The screen does the work.

        A message rather than a direct service call, so the pointer and the `/`
        key arrive at exactly one place — and so the early-departure
        confirmation lives on the screen that can push a modal.
        """

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Toggle())

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Only act when the switch disagrees with the truth.

        ``rebuild`` writes the switch back to whatever the database says, and a
        naive handler would treat that write as a user action and clock straight
        back out again.
        """
        event.stop()
        on_clock = self._ledger is not None and self._ledger.is_open
        if event.value != on_clock:
            self.post_message(self.Toggle())
