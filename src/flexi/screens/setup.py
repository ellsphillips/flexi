"""Setting Flexi up, on the same rail the terminal prompts are drawn on.

The wordmark lands at the top of this screen and the questions open out
underneath it, in one composition. The rail is the one `flexi init` uses: a
line down the left margin, a marker at the question being answered, no boxes.
Its glyphs come from `flexi.theme`.

One widget owns the whole line, so the marker has a position on it that can be
animated between rows.
"""

from __future__ import annotations

from typing import ClassVar, Unpack

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.reactive import Reactive, reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, Label, Select, Static

from flexi import wallclock
from flexi.components.options import ScreenOptions, StaticOptions, WidgetOptions
from flexi.components.wordmark import Wordmark
from flexi.constants import DEFAULT_DIVISION, Division
from flexi.domain import leaveyear
from flexi.screens.settings import ALL_REQUIRED, parse_answers
from flexi.services.registry import Services
from flexi.services.settings import (
    DEFAULT_ENTITLEMENT_DAYS,
    parse_entitlement_days,
    parse_month_day,
)
from flexi.theme import MARK_DONE, MARK_LIVE, RAIL_SETTLED, TAIL, colour

__all__ = (
    "ASK_WIDTH",
    "FIELD_WIDTH",
    "FORM_WIDTH",
    "GUTTER",
    "HEADING_ROWS",
    "LEAVE_YEAR_START",
    "NOTE_WIDTH",
    "QUESTION_ROWS",
    "RAIL_WIDTH",
    "RISE",
    "SLIDE",
    "TAIL_ROWS",
    "Question",
    "Rail",
    "SetupScreen",
    "entitlement_year",
    "form_rows",
    "sized",
)

LEAVE_YEAR_START = "04-06"
"""What the first question is pre-filled with, not the stored default."""

GUTTER = "  "
"""Indent to the left of the rail, so it sits off the edge of the terminal."""

HEADING_ROWS = 2
"""The heading, and the row of space under it."""

QUESTION_ROWS = 2
"""A question, and the row of space under it."""

TAIL_ROWS = 1
"""The foot of the rail."""

RISE = 0.45
"""Seconds the questions take to open out, and the wordmark to rise off them."""

SLIDE = 0.16
"""Seconds the marker takes to travel between two questions.

Long enough to read as travel, short enough that holding tab still moves.
"""

RAIL_WIDTH = 5
ASK_WIDTH = 22
FIELD_WIDTH = 24
NOTE_WIDTH = 36
FORM_WIDTH = RAIL_WIDTH + ASK_WIDTH + FIELD_WIDTH + NOTE_WIDTH
"""The four columns of a question, and the width of everything on this screen.

Fixed in Python, not left to `width: auto`: the wordmark has to match the
questions' width to centre over them, and an auto column takes the width of its
widest child.
"""


def sized(css: str) -> str:
    """Fill the column widths into a stylesheet.

    The widths are known in Python, so the CSS takes them from there instead of
    carrying a second copy.
    """
    for token, width in (
        ("RAIL_W", RAIL_WIDTH),
        ("ASK_W", ASK_WIDTH),
        ("FIELD_W", FIELD_WIDTH),
        ("NOTE_W", NOTE_WIDTH),
        ("FORM_W", FORM_WIDTH),
    ):
        css = css.replace(token, str(width))
    return css


class Rail(Static):
    """The line down the left of the form, and the marker travelling on it."""

    DEFAULT_CSS = sized("""
    Rail { width: RAIL_W; height: auto; }
    """)

    marker: Reactive[float] = reactive(0.0)
    """Which row the marker is on, as a float: Textual interpolates numbers."""

    def __init__(self, rows: int, **kwargs: Unpack[StaticOptions]) -> None:
        super().__init__(**kwargs)
        self._rows = rows

    def on_mount(self) -> None:
        self.styles.height = self._rows
        self._draw()

    def watch_marker(self) -> None:
        self._draw()

    def slide_to(self, row: int) -> None:
        """Animate the marker to a row."""
        self.animate("marker", value=float(row), duration=SLIDE, easing="out_cubic")

    def _draw(self) -> None:
        """The line, with the marker on it."""
        at = round(self.marker)
        line = Text(no_wrap=True)
        for row in range(self._rows):
            if row == 0:
                glyph, tone = MARK_DONE, colour("c-surplus")
            elif row == self._rows - 1:
                glyph, tone = TAIL, colour("c-line")
            elif row == at:
                glyph, tone = MARK_LIVE, colour("c-accent")
            else:
                glyph, tone = RAIL_SETTLED, colour("c-line")
            line.append(GUTTER)
            line.append(glyph, style=f"bold {tone}")
            line.append("\n")
        self.update(line)


class Question(Horizontal):
    """One moment on the rail: a label, a field and a note beyond it."""

    DEFAULT_CSS = sized("""
    Question {
        height: 1;
        width: auto;
        margin-bottom: 1;
    }
    Question .ask { width: ASK_W; color: $c-muted; }
    Question Input {
        width: FIELD_W;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
        color: $c-paper;
    }
    Question Select {
        width: FIELD_W;
        height: 1;
    }
    Question Select SelectCurrent { border: none; padding: 0; }
    Question .note { width: NOTE_W; padding-left: 2; color: $c-line; }
    Question.-live .ask { color: $c-paper; text-style: bold; }
    Question.-live .note { color: $c-muted; }
    """)

    def __init__(
        self,
        ask: str,
        field: Widget,
        note: str,
        **kwargs: Unpack[WidgetOptions],
    ) -> None:
        super().__init__(**kwargs)
        self._ask = ask
        self._field = field
        self._note = note

    def compose(self) -> ComposeResult:
        yield Label(self._ask, classes="ask")
        yield self._field
        yield Static(self._note, classes="note")


class SetupScreen(Screen[bool]):
    """First launch. Returns True when the answers are saved."""

    HELP_LABEL = "Setup"

    AUTO_FOCUS: ClassVar[str] = ""
    """Nothing is focused while the splash plays.

    Textual would otherwise focus the first input on mount, inside a block at
    zero height, and a key meant for the logo would land in it.
    `on_wordmark_landed` focuses the field once the questions are up.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    DEFAULT_CSS = sized("""
    SetupScreen { align: center middle; background: $c-ink; }

    #setup { width: FORM_W; height: auto; }

    /* No height and clipped, rather than `display: none`. The height is what is
       animated: as it opens the block is re-centred every frame, so the
       wordmark rises off the questions instead of jumping to make room. */
    #setup-questions {
        width: FORM_W;
        height: 0;
        opacity: 0;
        overflow: hidden;
    }

    #setup-form { width: FORM_W; height: auto; }
    #setup-asks { width: auto; height: auto; }

    #setup-heading { height: 1; margin-bottom: 1; color: $c-paper; text-style: bold; }
    #setup-tail { height: 1; color: $c-line; }
    """)

    def __init__(
        self,
        services: Services,
        *,
        animate: bool = False,
        **kwargs: Unpack[ScreenOptions],
    ) -> None:
        super().__init__(**kwargs)
        self._settings_svc = services.settings
        self._plays = animate

    def _asks(self) -> list[Question]:
        year = entitlement_year(LEAVE_YEAR_START)
        return [
            Question(
                "Leave year starts",
                Input(LEAVE_YEAR_START, id="input-leave-start", placeholder="MM-DD"),
                "6 April, for most schemes",
                id="ask-leave-start",
            ),
            Question(
                "Annual entitlement",
                Input(
                    str(DEFAULT_ENTITLEMENT_DAYS),
                    id="input-entitlement",
                    placeholder=str(DEFAULT_ENTITLEMENT_DAYS),
                ),
                f"days for {year}, halves allowed",
                id="ask-entitlement",
            ),
            Question(
                "Working days",
                Input("Mon-Fri", id="input-working-days", placeholder="Mon-Fri"),
                "or Tue, Thu if you work part time",
                id="ask-working-days",
            ),
            Question(
                "Bank holidays",
                Select(
                    Division.choices(),
                    value=DEFAULT_DIVISION.value,
                    id="select-division",
                ),
                "the GOV.UK division to follow",
                id="ask-division",
            ),
            Question(
                "Auto-close at",
                Input("18:00", id="input-auto-close", placeholder="HH:MM"),
                "when a session you forgot ends",
                id="ask-auto-close",
            ),
        ]

    def compose(self) -> ComposeResult:
        asks = self._asks()
        with Vertical(id="setup"):
            yield Wordmark(animate=self._plays, id="setup-wordmark")
            with Vertical(id="setup-questions"), Horizontal(id="setup-form"):
                yield Rail(form_rows(len(asks)), id="setup-rail")
                with Vertical(id="setup-asks"):
                    yield Static(
                        "Five questions, then it gets out of the way",
                        id="setup-heading",
                    )
                    yield from asks
                    yield Static(
                        "tab to move · enter to save · esc to cancel",
                        id="setup-tail",
                    )

    def on_input_changed(self, event: Input.Changed) -> None:
        """Keep the entitlement note on the year the typed start files it under."""
        if event.input.id != "input-leave-start":
            return
        try:
            year = entitlement_year(event.value)
        except ValueError:
            # Half a date is not an answer yet; the note keeps the last year
            # it could work out.
            return
        self.query_one("#ask-entitlement", Question).query_one(".note", Static).update(
            f"days for {year}, halves allowed"
        )

    def on_mount(self) -> None:
        """Tell the wordmark how wide to be.

        It centres its own content and is centred over the questions, so it has
        to match their width; the screen is what knows both.
        """
        self.query_one(Wordmark).styles.width = FORM_WIDTH

    # Arrival.

    def on_wordmark_landed(self, _event: Wordmark.Landed) -> None:
        """The word has stopped. Open the questions out underneath it.

        The height is counted with `form_rows`, never measured: measuring lays
        the questions out at full height first, which is the flash the
        animation exists to avoid.
        """
        questions = self.query_one("#setup-questions")
        questions.add_class("-arrived")
        rows = form_rows(len(self.query(Question)))
        questions.styles.animate(
            "height", value=rows, duration=RISE, easing="out_cubic"
        )
        questions.styles.animate("opacity", value=1.0, duration=RISE, delay=RISE / 2)
        self.query_one("#input-leave-start", Input).focus()

    def on_key(self, event: events.Key) -> None:
        """Any key during the animation cuts to the end of it.

        `ctrl+q` skips and still quits. Every other key is stopped, `escape`
        included, since cancelling this screen ends the program.
        """
        if not self.query_one("#setup-questions").has_class("-arrived"):
            self.query_one("#setup-wordmark", Wordmark).skip()
            if event.key != "ctrl+q":
                event.stop()

    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        self._mark_the_live_question()

    def _mark_the_live_question(self) -> None:
        """Emphasise the question holding the cursor, and send the marker to it."""
        focused = self.focused
        for index, question in enumerate(self.query(Question)):
            holds = focused is not None and question in focused.ancestors_with_self
            question.set_class(holds, "-live")
            if holds:
                self.query_one(Rail).slide_to(HEADING_ROWS + index * QUESTION_ROWS)

    # Saving.

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.action_save()

    def action_save(self) -> None:
        """Write the answers, or say which one is not an answer yet.

        Every answer is parsed before anything is written, then settings and
        entitlement commit together, on the boundary `SettingsScreen._save` uses.
        """
        entitlement_str = self.query_one("#input-entitlement", Input).value.strip()
        if not entitlement_str:
            self.notify(ALL_REQUIRED, severity="error")
            return
        try:
            entitlement = parse_entitlement_days(entitlement_str)
        except ValueError as error:
            self.notify(str(error), severity="error")
            return

        try:
            update = parse_answers(self)
        except ValueError as error:
            self.notify(str(error), severity="error")
            return

        # The leave year, not the calendar year: `get_active_entitlement_days`
        # looks an allowance up by the leave year it was filed under.
        year = leaveyear.active_year(wallclock.today(), *update.leave_year_start)
        self._settings_svc.save_settings_and_entitlements(update, {year: entitlement})

        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


def entitlement_year(start: str) -> int:
    """The leave year an allowance typed today would be filed under."""
    return leaveyear.active_year(wallclock.today(), *parse_month_day(start))


def form_rows(questions: int) -> int:
    """How tall the form is, in rows.

    The rail's height and the reveal's target height are both this, so the foot
    lands under the last question.
    """
    return HEADING_ROWS + questions * QUESTION_ROWS + TAIL_ROWS
