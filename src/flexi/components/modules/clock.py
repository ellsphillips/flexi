"""Are you on the clock, since when, and when can you go.

One key does the whole thing; the switch and the button are there for a pointer
and to show the key beside them.

The subtitle carries the elapsed time while a session is open and updates every
second, so the readout never looks stalled.
"""

from __future__ import annotations

from typing import ClassVar, Unpack

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Static, Switch

from flexi.components.common import Pill, Tone
from flexi.components.modules.base import Module
from flexi.components.options import ModuleOptions
from flexi.components.punch import PunchStrip
from flexi.domain.format import clock, hm, hms
from flexi.domain.ledger import DayLedger
from flexi.messages import Scope

__all__ = ("ClockModule",)


class ClockModule(Module):
    """Clock in, clock out, and see today at a glance."""

    HELP_LABEL = "Clock"

    WATCHES: ClassVar[Scope] = Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS

    def __init__(self, **kwargs: Unpack[ModuleOptions]) -> None:
        super().__init__(id="clock-module", title="Clock", subtitle="/", **kwargs)
        self._ledger: DayLedger | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="clock-state"):
            yield Pill("off the clock", id="clock-pill")
            yield Switch(value=False, id="clock-switch")
        yield PunchStrip(id="clock-strip", now=self.now)
        # Markup off: the line can carry a bank-holiday title from GOV.UK, and
        # that text is drawn, not interpreted.
        yield Static("", id="clock-detail", classes="caption", markup=False)
        yield Button("Arrive", id="clock-button", classes="-primary")

    def on_mount(self) -> None:
        self.rebuild()

    # --- drawing ----------------------------------------------------------

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

        # Plain assignment, so the slider animates: `set_reactive` skips the
        # watcher, and the watcher is the animation. The Changed event it posts
        # is a no-op, `_ledger` having just been refreshed.
        self.query_one("#clock-switch", Switch).value = on_clock

        button = self.query_one("#clock-button", Button)
        button.label = "Depart" if on_clock else "Arrive"

        self.query_one("#clock-strip", PunchStrip).set_ledger(
            ledger, window=services.ledger.window, now=self.now
        )
        self.query_one("#clock-detail", Static).update(self._detail(ledger))
        self.tick()

    def tick(self) -> None:
        """Refresh only what changes second to second.

        The date is numeric and padded: `27/08/2026` keeps the same width all
        month where `Thu 27 Aug` does not, and a slot that changes width every
        day moves the panel edge.
        """
        today = f"{self.now.date():%d/%m/%Y}"
        if self._ledger is None or not self._ledger.is_open:
            self.set_subtitle(today)
            return
        self.set_subtitle(f"{hms(self._ledger.worked)} · {today}")

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
        leave_at = ledger.leave_at
        if leave_at is not None and ledger.is_open:
            parts.append(f"go home {clock(leave_at)}")
        else:
            parts.append(f"worked {hm(ledger.worked)}")
        # No break time: the line is too narrow to hold another term, and the
        # records table has the breakdown one `space` away.
        return " · ".join(parts)

    # --- interaction ------------------------------------------------------

    class Toggle(Message):
        """The user asked to clock in or out. The screen does the work.

        A message, not a service call: the pointer and the `/` key arrive at one
        place, and the early-departure confirmation belongs on the screen, which
        can push a modal.
        """

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Toggle())

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Only act when the switch disagrees with the truth.

        ``rebuild`` writes the switch back to whatever the database says, and
        that write posts ``Changed`` just as a press does.
        """
        event.stop()
        on_clock = self._ledger is not None and self._ledger.is_open
        if event.value != on_clock:
            self.post_message(self.Toggle())
