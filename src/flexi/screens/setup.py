"""Setting Flexi up, on the same rail the terminal prompts are drawn on.

The wordmark turns in at the top of this screen and stays there; the questions
arrive underneath it once it has stopped. One screen, one composition -- rather
than an animation on a screen of its own, dismissed to reveal a bordered dialog
that looked like it came from somewhere else.

The rail is the same one `flexi init` uses to ask what to do about an existing
database: a line down the left margin, heavy through the question being answered
and hairline through the rest, a marker at each moment, no boxes. Its glyphs
come from `flexi.theme` so there is one design system rather than two.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, Label, Select, Static

from flexi.components.wordmark import Wordmark
from flexi.services.registry import Services
from flexi.theme import MARK_DONE, MARK_LIVE, RAIL_SETTLED, TAIL

DIVISIONS = [
    ("England & Wales", "england-and-wales"),
    ("Scotland", "scotland"),
    ("Northern Ireland", "northern-ireland"),
]

GUTTER = "  "
"""Indent to the left of the rail, so it sits off the edge of the terminal."""


class Question(Horizontal):
    """One moment on the rail: a marker, a label, a field and a note."""

    DEFAULT_CSS = """
    Question {
        height: 1;
        width: auto;
        margin-bottom: 1;
    }
    Question .rail { width: 5; color: $c-line; }
    Question .ask { width: 22; color: $c-muted; }
    Question Input {
        width: 24;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
        color: $c-paper;
    }
    Question Select {
        width: 24;
        height: 1;
    }
    Question Select SelectCurrent { border: none; padding: 0; }
    Question .note { width: 36; padding-left: 2; color: $c-line; }
    Question.-live .rail { color: $c-accent; text-style: bold; }
    Question.-live .ask { color: $c-paper; text-style: bold; }
    Question.-live .note { color: $c-muted; }
    """

    def __init__(self, ask: str, field: Widget, note: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ask = ask
        self._field = field
        self._note = note

    def compose(self) -> ComposeResult:
        yield Static(f"{GUTTER}{RAIL_SETTLED}  ", classes="rail")
        yield Label(self._ask, classes="ask")
        yield self._field
        yield Static(self._note, classes="note")

    def set_live(self, *, live: bool) -> None:
        """Mark this the question being answered, or one of the others."""
        self.set_class(live, "-live")
        glyph = MARK_LIVE if live else RAIL_SETTLED
        self.query_one(".rail", Static).update(f"{GUTTER}{glyph}  ")


class SetupScreen(Screen[bool]):
    """First launch. Returns True when the answers are saved."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    DEFAULT_CSS = """
    SetupScreen { align: center middle; background: $c-ink; }

    #setup { width: auto; height: auto; }

    #setup-questions { width: auto; height: auto; display: none; }
    #setup-questions.-arrived { display: block; }

    #setup-heading { color: $c-paper; text-style: bold; padding-bottom: 1; }
    #setup-tail { color: $c-line; padding-top: 1; }
    """

    def __init__(
        self, services: Services, *, animate: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._services = services
        self._settings_svc = services.settings
        self._plays = animate

    def compose(self) -> ComposeResult:
        year = self._settings_svc.active_leave_year()
        with Vertical(id="setup"):
            yield Wordmark(animate=self._plays, id="setup-wordmark")
            with Vertical(id="setup-questions"):
                yield Static(
                    f"{GUTTER}{MARK_DONE}  Five questions, then it gets out of the way",
                    id="setup-heading",
                )
                yield Question(
                    "Leave year starts",
                    Input("04-06", id="input-leave-start", placeholder="MM-DD"),
                    "6 April, for most schemes",
                    id="ask-leave-start",
                )
                yield Question(
                    "Annual entitlement",
                    Input("25.0", id="input-entitlement", placeholder="25.0"),
                    f"days for {year}, halves allowed",
                    id="ask-entitlement",
                )
                yield Question(
                    "Working days",
                    Input("Mon-Fri", id="input-working-days", placeholder="Mon-Fri"),
                    "or Tue, Thu if part time",
                    id="ask-working-days",
                )
                yield Question(
                    "Bank holidays",
                    Select(DIVISIONS, value="england-and-wales", id="select-division"),
                    "the GOV.UK division to follow",
                    id="ask-division",
                )
                yield Question(
                    "Auto-close at",
                    Input("18:00", id="input-auto-close", placeholder="HH:MM"),
                    "when a session you forgot ends",
                    id="ask-auto-close",
                )
                yield Static(
                    f"{GUTTER}{TAIL}  tab to move · enter to save · esc to cancel",
                    id="setup-tail",
                )

    # -- arrival -----------------------------------------------------------

    def on_wordmark_landed(self, _event: Wordmark.Landed) -> None:
        """The word has stopped. Bring the questions up under it."""
        self.query_one("#setup-questions").add_class("-arrived")
        self.query_one("#input-leave-start", Input).focus()

    def on_key(self, event: object) -> None:
        """Any key during the animation cuts to the end of it.

        Somebody setting Flexi up a second time should not have to watch it
        again, and a splash that cannot be skipped is a splash that is in the
        way. Once the questions are up the keys belong to them.
        """
        wordmark = self.query_one("#setup-wordmark", Wordmark)
        if not self.query_one("#setup-questions").has_class("-arrived"):
            wordmark.skip()
            event.stop()  # type: ignore[attr-defined]

    def on_descendant_focus(self, _event: object) -> None:
        self._mark_the_live_question()

    def _mark_the_live_question(self) -> None:
        """Heavy rail through whichever question currently has the cursor."""
        focused = self.focused
        for question in self.query(Question):
            holds = focused is not None and question in focused.ancestors_with_self
            question.set_live(live=holds)

    # -- saving ------------------------------------------------------------

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.action_save()

    def action_save(self) -> None:
        leave_start = self.query_one("#input-leave-start", Input).value.strip()
        entitlement_str = self.query_one("#input-entitlement", Input).value.strip()
        working_days = self.query_one("#input-working-days", Input).value.strip()
        division = self.query_one("#select-division", Select).value
        auto_close = self.query_one("#input-auto-close", Input).value.strip()

        if not all([leave_start, entitlement_str, working_days, auto_close]):
            self.notify("All fields are required", severity="error")
            return

        try:
            entitlement = float(entitlement_str)
        except ValueError:
            self.notify("Invalid entitlement value", severity="error")
            return

        if not isinstance(division, str):
            self.notify("Please select a bank holiday region", severity="error")
            return

        try:
            self._settings_svc.save_settings(
                leave_year_start=leave_start,
                working_days=working_days,
                bank_holiday_division=division,
                auto_close_time=auto_close,
            )
        except ValueError as error:
            self.notify(str(error), severity="error")
            return

        # The leave year, not the calendar year. Setting Flexi up in February
        # against an April leave year files the allowance under the year that
        # has not started, and get_active_entitlement_days then finds nothing.
        year = self._settings_svc.active_leave_year()
        self._settings_svc.save_entitlement(year, entitlement)

        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
