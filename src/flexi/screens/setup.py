"""Setting Flexi up, on the same rail the terminal prompts are drawn on.

The wordmark turns in at the top of this screen and stays there; the questions
arrive underneath it once it has stopped. One screen, one composition -- rather
than an animation on a screen of its own, dismissed to reveal a bordered dialog
that looked like it came from somewhere else.

The rail is the same one `flexi init` uses to ask what to do about an existing
database: a line down the left margin, a marker at the moment being answered, no
boxes. Its glyphs come from `flexi.theme`, so there is one design system rather
than two that happen to agree.

It is drawn as a single widget rather than a piece per question. A rail made of
pieces has a gap wherever the rows are spaced, which is a dotted line pretending
to be a continuous one -- and, more to the point, a marker made of pieces can
only blink from one to the next. One widget owning the whole line means the
marker has a position on it, and a position is a thing that can be moved.
"""

from __future__ import annotations

from typing import Any, ClassVar

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
from flexi.components.wordmark import Wordmark
from flexi.constants import DEFAULT_DIVISION, Division
from flexi.domain import leaveyear
from flexi.screens.settings import ALL_REQUIRED, parse_answers
from flexi.services.registry import Services
from flexi.services.settings import DEFAULT_ENTITLEMENT_DAYS
from flexi.theme import MARK_DONE, MARK_LIVE, RAIL_SETTLED, TAIL, colour

__all__ = (
    "ASK_WIDTH",
    "FIELD_WIDTH",
    "FORM_WIDTH",
    "GUTTER",
    "HEADING_ROWS",
    "NOTE_WIDTH",
    "QUESTION_ROWS",
    "RAIL_WIDTH",
    "RISE",
    "SLIDE",
    "TAIL_ROWS",
    "Question",
    "Rail",
    "SetupScreen",
    "form_rows",
    "sized",
)

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

Long enough to be seen as travel rather than a jump, short enough that holding
tab still feels like moving rather than like waiting."""

RAIL_WIDTH = 5
ASK_WIDTH = 22
FIELD_WIDTH = 24
NOTE_WIDTH = 36
FORM_WIDTH = RAIL_WIDTH + ASK_WIDTH + FIELD_WIDTH + NOTE_WIDTH
"""The four columns of a question, and the width of everything on this screen.

Written down rather than left to `width: auto`, because the wordmark has to be
the same width as the questions to be centred over them. An auto-width column is
exactly as wide as its widest child, so a narrower wordmark sat against its left
edge -- correctly centred as a block, visibly off-centre as a logo."""


def sized(css: str) -> str:
    """Fill the column widths into a stylesheet.

    The widths have to be known in Python -- the wordmark is told to be as wide
    as the questions so it can be centred over them -- and repeating them in the
    CSS is how the two quietly stop agreeing.
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
    """Which row the marker is on. A float, because it is animated between rows
    and Textual can only interpolate numbers."""

    def __init__(self, rows: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rows = rows

    def on_mount(self) -> None:
        self.styles.height = self._rows
        self._draw()

    def watch_marker(self) -> None:
        self._draw()

    def slide_to(self, row: int) -> None:
        """Send the marker to a row, travelling rather than jumping."""
        self.animate("marker", value=float(row), duration=SLIDE, easing="out_cubic")

    def _draw(self) -> None:
        """The line, with the marker on it.

        The marker is the only thing lit. Lighting the row beneath it as well,
        to pick out the whole two-row segment a question occupies, made the rail
        busier without saying anything the diamond had not already said.

        The foot wears the same grey as the rest of the line. It is structure,
        not content, and a brighter one drew the eye to the end of the form.
        """
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

    def __init__(self, ask: str, field: Widget, note: str, **kwargs: Any) -> None:
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
        self, services: Services, *, animate: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._settings_svc = services.settings
        self._plays = animate

    def _asks(self) -> list[Question]:
        year = self._settings_svc.active_leave_year()
        return [
            Question(
                "Leave year starts",
                Input("04-06", id="input-leave-start", placeholder="MM-DD"),
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

    def on_mount(self) -> None:
        """Tell the wordmark how wide to be.

        It has to be exactly as wide as the questions, because it centres its
        own content and is centred over them. Left to itself it is as wide as
        the canvas, which is narrower, and a narrower widget sits against the
        left edge of the column -- correctly centred as a block, and visibly off
        to one side as a logo. The screen is the only thing that knows both
        widths, so the screen is what says it.
        """
        self.query_one(Wordmark).styles.width = FORM_WIDTH

    # -- arrival -----------------------------------------------------------

    def on_wordmark_landed(self, _event: Wordmark.Landed) -> None:
        """The word has stopped. Open the questions out underneath it.

        The height is animated rather than switched on, because the whole block
        is centred: growing it pushes the wordmark up a row at a time, which
        reads as the logo making room. Switching it on moved everything at once
        and read as a redraw.

        The height is counted rather than measured. Measuring means laying the
        questions out at full height first, which is the flash this exists to
        avoid; counting is checked against the real thing by a test, so it
        cannot drift without saying so.
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

        Somebody setting Flexi up a second time should not have to watch it
        again, and a splash that cannot be skipped is a splash that is in the
        way. Once the questions are up the keys belong to them.
        """
        if not self.query_one("#setup-questions").has_class("-arrived"):
            self.query_one("#setup-wordmark", Wordmark).skip()
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

    # -- saving ------------------------------------------------------------

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.action_save()

    def action_save(self) -> None:
        """Write the answers, or say which one is not an answer yet.

        Every answer is parsed before anything is written, then settings and
        entitlement commit together -- the same boundary `SettingsScreen._save`
        uses.
        """
        entitlement_str = self.query_one("#input-entitlement", Input).value.strip()
        if not entitlement_str:
            self.notify(ALL_REQUIRED, severity="error")
            return
        try:
            entitlement = float(entitlement_str)
        except ValueError:
            self.notify("Entitlement must be a number of days", severity="error")
            return

        try:
            update = parse_answers(self)
        except ValueError as error:
            self.notify(str(error), severity="error")
            return

        # The leave year, not the calendar year. Setting Flexi up in February
        # against an April leave year files the allowance under the year that
        # has not started, and get_active_entitlement_days then finds nothing.
        year = leaveyear.active_year(wallclock.today(), *update.leave_year_start)
        self._settings_svc.save_settings_and_entitlements(update, {year: entitlement})

        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


def form_rows(questions: int) -> int:
    """How tall the form is, in rows.

    The rail has to be exactly this tall for its foot to land under the last
    question, and the reveal animates the block open to exactly this height, so
    it is worked out once and asked for twice.
    """
    return HEADING_ROWS + questions * QUESTION_ROWS + TAIL_ROWS
