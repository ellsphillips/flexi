"""The year grid: the arithmetic, the glyphs and the cursor, on their own.

The leave screen drives this through a Pilot, which is the right instrument for
"f2 books Thursday" and the wrong one for "what does a four-column tile draw" --
a year of ledgers and a booking round trip per question. So the grid is asked
here instead, as a bare widget in an app carrying nothing but the real
stylesheet. The stylesheet is not optional: a tile whose type resolves to no
rule paints in the widget's own ground, which is how a booked fortnight comes to
look like an empty one.

Colour carries the type of a booking and the glyph carries its portion, so both
are asserted, and neither is allowed to stand in for the other.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import PurePath
from types import SimpleNamespace
from typing import ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Offset
from textual.message import Message
from textual.pilot import Pilot

from flexi.components.yearcalendar import (
    AFTERNOON,
    BLANK,
    FULL,
    HOLIDAY,
    MIN_CELL,
    MORNING,
    SPLIT,
    YearCalendar,
    legend,
)
from flexi.config import CONFIG
from flexi.constants import AbsenceType, DayKind, Portion
from flexi.domain.dates import DAYS_IN_WEEK
from flexi.domain.ledger import AbsenceSlice, DayLedger
from flexi.domain.stitch import Selection
from flexi.theme import THEME_NAME, THEME_PATH, flexi_theme

PACKAGE = THEME_PATH.parent.parent
CONTRACTED = timedelta(hours=7, minutes=24)

JUNE = date(2026, 6, 1)
"""A Monday, so June 2026 begins in the first column and July does not."""

JULY_END = date(2026, 7, 31)
SATURDAY = date(2026, 6, 6)
WIDE_PANEL = 60
"""Seven columns of eight or nine: wide enough for a tile to carry a word."""


class Harness(App[None]):
    """One calendar, the real palette, and an inbox for what it posts."""

    CSS_PATH: ClassVar[list[str | PurePath]] = [
        PACKAGE / "theme" / "flexi.tcss",
        PACKAGE / "styles" / "leave.tcss",
    ]

    def __init__(self, calendar: YearCalendar, *, width: int, height: int) -> None:
        super().__init__()
        self.register_theme(flexi_theme())
        self.theme = THEME_NAME
        self.calendar = calendar
        self.calendar.styles.width = width
        self.calendar.styles.height = height
        self.posted: list[Message] = []

    def compose(self) -> ComposeResult:
        yield self.calendar

    def on_year_calendar_selection_changed(
        self, message: YearCalendar.SelectionChanged
    ) -> None:
        self.posted.append(message)


@asynccontextmanager
async def mounted(
    calendar: YearCalendar, *, width: int = WIDE_PANEL, height: int = 20
) -> AsyncIterator[Pilot[None]]:
    app = Harness(calendar, width=width, height=height)
    async with app.run_test(size=(80, 24)) as pilot:
        yield pilot


def posted(pilot: Pilot[None]) -> list[Message]:
    app = pilot.app
    assert isinstance(app, Harness)
    return app.posted


def day(
    when: date,
    *,
    working: bool = True,
    holiday: str | None = None,
    absences: tuple[AbsenceSlice, ...] = (),
) -> DayLedger:
    """One day of the year, in whatever state the test is about."""
    kind = DayKind.WORKING if working else DayKind.WEEKEND
    if holiday is not None:
        kind = DayKind.HOLIDAY
    elif absences:
        kind = DayKind.ABSENT
    return DayLedger(
        date=when,
        kind=kind,
        is_working_day=working,
        contracted=CONTRACTED,
        worked=timedelta(),
        expected=CONTRACTED if working and not absences else timedelta(),
        holiday_title=holiday,
        absences=absences,
    )


def booked(
    kind: AbsenceType, portion: Portion = Portion.FULL, absence_id: int = 1
) -> AbsenceSlice:
    return AbsenceSlice(absence_id, kind, portion)


@asynccontextmanager
async def shown(
    ledgers: dict[date, DayLedger] | None = None,
    *,
    start: date = JUNE,
    end: date = JULY_END,
    today: date = JUNE,
    first_weekday: int = 0,
    width: int = WIDE_PANEL,
    height: int = 20,
) -> AsyncIterator[YearCalendar]:
    """A calendar showing June and July 2026, laid out and drawn."""
    calendar = YearCalendar()
    async with mounted(calendar, width=width, height=height) as pilot:
        calendar.show(
            start, end, ledgers or {}, today=today, first_weekday=first_weekday
        )
        await pilot.pause()
        yield calendar


def click_at(calendar: YearCalendar, x: int, y: int) -> None:
    """Click a cell by content offset.

    `on_click` reads one field off the event and is typed for it, and driving a
    real Pilot click costs a second where these cost nothing. One test below
    does use the mouse, to say the handler is wired to it at all.
    """
    calendar.on_click(SimpleNamespace(offset=Offset(x, y)))


def text_of(calendar: YearCalendar, line: int) -> str:
    return calendar.render_line(line).text


# -- how wide a day is -------------------------------------------------------


async def test_the_seven_columns_share_the_whole_panel_between_them() -> None:
    """A day is a tile that paints its own ground.

    Any width the columns do not take is a slab of unpainted panel down the side
    of the grid, which reads as a rendering fault rather than as a margin.
    """
    calendar = YearCalendar()
    async with mounted(calendar, width=WIDE_PANEL):
        assert sum(calendar.columns) == WIDE_PANEL
        assert calendar.grid_width == WIDE_PANEL


async def test_the_odd_columns_out_are_spread_one_at_a_time() -> None:
    """Sixty does not divide by seven, so three columns are a character wider.

    Dumping the whole remainder on the last column instead would leave a Sunday
    four characters wider than every other day of the week.
    """
    calendar = YearCalendar()
    async with mounted(calendar, width=WIDE_PANEL):
        assert calendar.columns == (9, 9, 9, 9, 8, 8, 8)
        assert calendar.cell == 8


async def test_no_column_is_ever_more_than_a_character_wider_than_another() -> None:
    """The rule holds at every width, not just the one the panel happens to be.

    Sixty is the width this panel is drawn at today and is the one width where
    almost any spreading rule looks right. A remainder dumped on the last column
    would draw a Sunday six characters wider than its Monday on a panel one off
    a multiple of seven, and an equal tile is what the eye counts days by.
    """
    calendar = YearCalendar()
    narrowest = DAYS_IN_WEEK * MIN_CELL
    async with mounted(calendar) as pilot:
        # Two full turns of the remainder: every width leaves a different number
        # of columns over, and 0 and 6 are the two that get spreading wrong.
        for width in range(narrowest, narrowest + 2 * DAYS_IN_WEEK):
            calendar.styles.width = width
            await pilot.pause()
            columns = calendar.columns
            assert len(columns) == DAYS_IN_WEEK
            assert sum(columns) == width, f"the grid does not fill a {width} panel"
            assert max(columns) - min(columns) <= 1, f"a ragged week at {width}"


async def test_a_panel_too_narrow_for_a_week_stops_shrinking() -> None:
    """Below four columns a day loses either a digit or its marker.

    The grid overflows the panel and scrolls instead, which keeps every day
    legible on a terminal nobody should be running this on.
    """
    calendar = YearCalendar()
    async with mounted(calendar, width=14):
        assert calendar.grid_width == DAYS_IN_WEEK * MIN_CELL
        assert calendar.cell == MIN_CELL


async def test_resizing_the_panel_relays_the_grid_out() -> None:
    """A resize re-lays the grid out.

    The columns are a function of the panel width and nothing else recomputes
    them, so a grid still laid out at the old width leaves the last day of the
    week hanging over the edge until something unrelated forces a redraw.
    """
    calendar = YearCalendar()
    async with mounted(calendar) as pilot:
        calendar.show(JUNE, JULY_END, {}, today=JUNE)
        await pilot.pause()
        assert calendar.virtual_size.width == WIDE_PANEL

        calendar.styles.width = 42
        await pilot.pause()
        assert calendar.grid_width == 42
        assert calendar.virtual_size.width == 42


# -- what is laid out --------------------------------------------------------


async def test_every_month_gets_a_title_and_its_own_whole_weeks() -> None:
    """Months share the seven columns but never a row.

    June 2026 ends on a Tuesday; carrying July's first days into that row would
    put two dates in one cell and make the cursor ambiguous.
    """
    async with shown() as calendar:
        titles = [block.title for block, _ in calendar.month_rows()]
        assert titles == ["June 2026", "July 2026"]
        # One heading, then a title and five weeks for each of the two months.
        assert calendar.virtual_size.height == 1 + (1 + 5) * 2


async def test_the_column_headings_follow_the_configured_first_day() -> None:
    """The headings rotate with the first day of the week.

    A week starting on Sunday under headings reading "M T W T F S S" is a grid
    that looks one day out for the whole year.
    """
    async with shown(first_weekday=6) as calendar:
        assert text_of(calendar, 0).split() == ["S", "M", "T", "W", "T", "F", "S"]


async def test_a_month_names_itself_and_rules_across_to_the_edge() -> None:
    """A month title is a seam, ruled rather than boxed.

    Every divider in this interface is a rule, and a box around each month would
    cost two rows a month of a year that already scrolls.
    """
    async with shown() as calendar:
        title = text_of(calendar, 1)
        assert title.startswith(" June 2026 ")
        assert set(title.removeprefix(" June 2026 ")) == {"─"}


async def test_the_days_before_a_month_starts_are_blank_rather_than_borrowed() -> None:
    """July 2026 starts on a Wednesday, and the two cells before it are empty.

    Filling them with the last days of June would draw those days twice, which
    makes the cursor ambiguous and a selection uncountable.
    """
    async with shown() as calendar:
        july = calendar.row_of(date(2026, 7, 1))
        assert july is not None
        week = text_of(calendar, july)
        lead = calendar.columns[0] + calendar.columns[1]
        assert week[:lead].strip() == ""
        assert week[lead:].lstrip().startswith("1")


async def test_a_line_below_the_last_month_is_blank() -> None:
    """The panel is taller than a two-month span.

    A widget that ran off the end of its own rows would repeat the last week
    down the rest of the panel.
    """
    async with shown() as calendar:
        assert text_of(calendar, calendar.virtual_size.height).strip() == ""


# -- what a tile says --------------------------------------------------------


async def test_a_tile_with_room_spells_out_what_is_booked_on_it() -> None:
    """Nine columns is where the type stops being carried by colour alone."""
    when = date(2026, 6, 11)
    async with shown({when: day(when, absences=(booked(AbsenceType.ANNUAL),))}) as cal:
        assert cal._day_segment(when, 12).text == " 11 annual  "


async def test_a_narrow_tile_carries_the_number_and_a_glyph() -> None:
    """Under nine columns there is no room for a word.

    The glyph carries the portion and the colour carries the type, and the panel
    beside the grid is what spells the selected day out in full.
    """
    when = date(2026, 6, 11)
    async with shown({when: day(when, absences=(booked(AbsenceType.ANNUAL),))}) as cal:
        assert cal._day_segment(when, 5).text == f" 11{FULL}{BLANK}"


async def test_a_tile_paints_every_column_it_was_given() -> None:
    """A tile paints every column it was given, including the gutter.

    Left unstyled, the blanks take the widget's own ground and read as unpainted
    slabs running the length of the grid.
    """
    when = date(2026, 6, 11)
    async with shown({when: day(when)}) as calendar:
        for width in (4, 9, 14):
            assert len(calendar._day_segment(when, width).text) == width


@pytest.mark.parametrize(
    ("ledger", "expected"),
    [
        pytest.param(None, "", id="a day outside the year says nothing"),
        pytest.param(day(JUNE), "", id="an ordinary working day says nothing"),
        pytest.param(day(JUNE, holiday="Whitsun"), "hol", id="a bank holiday"),
        pytest.param(
            day(JUNE, absences=(booked(AbsenceType.FLEXI),)),
            "toil",
            id="TOIL is booked as flexi and read as TOIL",
        ),
        pytest.param(
            day(JUNE, absences=(booked(AbsenceType.SICK, Portion.AM),)),
            f"sick {MORNING}",
            id="a half day names the half",
        ),
        pytest.param(
            day(
                JUNE,
                absences=(
                    booked(AbsenceType.ANNUAL, Portion.AM),
                    booked(AbsenceType.FLEXI, Portion.PM, absence_id=2),
                ),
            ),
            "part day",
            id="two bookings will not both fit",
        ),
    ],
)
async def test_a_tile_says_what_is_on_the_day(
    ledger: DayLedger | None, expected: str
) -> None:
    """The word is the reason the grid is not colour alone.

    A day carrying two half-day bookings has no room to name either, so it says
    that it is part booked and leaves the rail to say what of.
    """
    async with shown() as calendar:
        assert calendar._label(ledger) == expected


@pytest.mark.parametrize(
    ("ledger", "expected"),
    [
        pytest.param(None, BLANK, id="a day outside the year"),
        pytest.param(day(JUNE), BLANK, id="an ordinary working day"),
        pytest.param(day(JUNE, holiday="Whitsun"), HOLIDAY, id="a bank holiday"),
        pytest.param(
            day(JUNE, absences=(booked(AbsenceType.ANNUAL),)),
            FULL,
            id="a whole day booked",
        ),
        pytest.param(
            day(JUNE, absences=(booked(AbsenceType.ANNUAL, Portion.PM),)),
            AFTERNOON,
            id="an afternoon booked",
        ),
        pytest.param(
            day(
                JUNE,
                absences=(
                    booked(AbsenceType.ANNUAL, Portion.AM),
                    booked(AbsenceType.ANNUAL, Portion.PM, absence_id=2),
                ),
            ),
            FULL,
            id="two halves of the same kind make a whole day",
        ),
        pytest.param(
            day(
                JUNE,
                absences=(
                    booked(AbsenceType.SICK, Portion.AM),
                    booked(AbsenceType.ANNUAL, Portion.PM, absence_id=2),
                ),
            ),
            SPLIT,
            id="a morning sick and an afternoon off is neither",
        ),
    ],
)
async def test_the_glyph_says_how_much_of_the_day_is_gone(
    ledger: DayLedger | None, expected: str
) -> None:
    """The glyph says how much of the day is gone.

    The tile has one character for this and its colour is already spoken for by
    the type, so a morning off drawn as a full day reads as a day of leave
    somebody never booked.
    """
    async with shown() as calendar:
        assert calendar._marker(ledger) == expected


async def test_a_booked_day_reaches_the_drawn_line_with_its_glyph_and_its_ground() -> (
    None
):
    """The tile has to arrive on the line, in the column its date sits in.

    Everything else here asks the helper that shapes one tile. Nothing says the
    week strip hands that helper the right column, or that the style survives
    onto the line — and a booking drawn a column to the left is a day marked
    against a date nobody booked.
    """
    thursday = date(2026, 6, 11)
    ledgers = {thursday: day(thursday, absences=(booked(AbsenceType.ANNUAL),))}
    async with shown(ledgers, width=DAYS_IN_WEEK * MIN_CELL) as calendar:
        line = calendar.row_of(thursday)
        assert line is not None
        strip = calendar.render_line(line)
        assert strip.text == " 8   9  10  11● 12  13  14  "

        booked_tile = list(strip)[thursday.weekday()]
        assert booked_tile.text == f"11{FULL} "
        assert booked_tile.style == calendar.get_component_rich_style("cal--annual")


# -- what a tile is coloured -------------------------------------------------


@pytest.mark.parametrize(
    ("ledger", "component"),
    [
        pytest.param(None, "cal--outside", id="before the leave year began"),
        pytest.param(day(SATURDAY, working=False), "cal--weekend", id="a Saturday"),
        pytest.param(day(SATURDAY), "cal--day", id="an ordinary working day"),
        pytest.param(
            day(SATURDAY, holiday="Whitsun"), "cal--holiday", id="a bank holiday"
        ),
        pytest.param(
            day(SATURDAY, absences=(booked(AbsenceType.ANNUAL),)),
            "cal--annual",
            id="annual leave",
        ),
        pytest.param(
            day(SATURDAY, absences=(booked(AbsenceType.FLEXI),)),
            "cal--toil",
            id="TOIL takes the colour of its display name, not its stored one",
        ),
    ],
)
async def test_a_tile_takes_the_ground_of_whatever_is_on_it(
    ledger: DayLedger | None, component: str
) -> None:
    """Every one of these has to resolve to a rule in the stylesheet.

    A component class no rule matches falls back to the widget's own ground, so
    a booked week and an empty one paint identically and the only report of the
    booking is the panel beside the grid.
    """
    async with shown() as calendar:
        assert calendar._day_style(SATURDAY, ledger) == (
            calendar.get_component_rich_style(component)
        )


async def test_where_the_cursor_is_beats_what_is_booked_there() -> None:
    """The cursor and the selection outrank whatever is booked underneath them.

    Where you are is more urgent than what is on it, and the rail spells the
    booking out anyway — where a cursor that vanished onto a booked day would
    leave the arrow keys moving something nobody can see.
    """
    when = date(2026, 6, 11)
    ledger = day(when, absences=(booked(AbsenceType.ANNUAL),))
    async with shown({when: ledger}) as calendar:
        calendar.go_to(when)
        assert calendar._day_style(when, ledger) == (
            calendar.get_component_rich_style("cal--cursor")
        )

        calendar.action_extend(2)
        assert calendar._day_style(when, ledger) == (
            calendar.get_component_rich_style("cal--selected")
        )


async def test_today_is_underlined_on_top_of_whatever_it_landed_on() -> None:
    """Today is underlined on top of whatever it landed on.

    It can coincide with a bank holiday, a booked day or a Sunday, so it adds to
    the ground rather than replacing it: replacing it would lose the booking.
    """
    when = date(2026, 6, 11)
    ledger = day(when, holiday="Whitsun")
    async with shown({when: ledger}, today=when) as calendar:
        style = calendar._day_style(when, ledger)
        assert style == calendar.get_component_rich_style(
            "cal--holiday"
        ) + calendar.get_component_rich_style("cal--today")
        assert style.underline


# -- where a day is ----------------------------------------------------------


async def test_a_date_sits_on_the_line_its_week_is_drawn_on() -> None:
    """Everything that scrolls or clicks goes through this."""
    async with shown() as calendar:
        assert calendar.row_of(JUNE) == 2
        assert calendar.row_of(date(2026, 6, 8)) == 3


async def test_a_date_the_calendar_is_not_showing_sits_nowhere() -> None:
    """A date the calendar is not showing sits on no line.

    `go_to` is handed dates from the command line and from a jump box, and
    scrolling to a line that does not exist would move the panel arbitrarily.
    """
    async with shown() as calendar:
        before = calendar.scroll_offset
        assert calendar.row_of(date(2027, 1, 1)) is None
        calendar.scroll_to_day(date(2027, 1, 1))
        assert calendar.scroll_offset == before


async def test_only_the_months_on_screen_are_offered_as_jump_targets() -> None:
    """Only the months on screen are offered as jump targets.

    A badge over a month nobody can see is a keystroke that appears to do
    nothing, so the offer is made against the drawn lines rather than the year.
    """
    calendar = YearCalendar()
    async with mounted(calendar, height=8) as pilot:
        calendar.show(JUNE, JULY_END, {}, today=JUNE)
        await pilot.pause()
        assert [block.title for block, _ in calendar.visible_months()] == [
            "June 2026",
            "July 2026",
        ]

        calendar.scroll_to(y=5, animate=False)
        await pilot.pause()
        assert [(block.title, line) for block, line in calendar.visible_months()] == [
            ("July 2026", 2)
        ]


async def test_moving_the_cursor_keeps_a_row_of_context_above_it() -> None:
    """The cursor is scrolled to with a row of context above it.

    Pinned to the last line of the panel it leaves nowhere to read the week it
    is about to move into.
    """
    calendar = YearCalendar()
    async with mounted(calendar, height=8) as pilot:
        calendar.show(JUNE, JULY_END, {}, today=JUNE)
        await pilot.pause()
        calendar.go_to(JULY_END)
        await pilot.pause()

        line = calendar.row_of(JULY_END)
        assert line is not None
        assert 0 < calendar.scroll_offset.y <= line - 1


# -- moving ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "days"),
    [
        pytest.param("h", -1, id="h is a day back"),
        pytest.param("l", 1, id="l is a day on"),
        pytest.param("k", -7, id="k is a week back"),
        pytest.param("j", 7, id="j is a week on"),
    ],
)
async def test_a_vim_key_moves_by_the_same_step_as_the_arrow_beside_it(
    key: str, days: int
) -> None:
    """A week is a row, so `j` and `k` have to be seven days rather than one.

    Somebody who reaches for `hjkl` on every other list in this interface and
    finds them dead on the calendar has to take a hand off the home row for the
    one screen where the cursor moves most.
    """
    calendar = YearCalendar()
    async with mounted(calendar) as pilot:
        calendar.show(JUNE, JULY_END, {}, today=JUNE)
        calendar.go_to(date(2026, 6, 11))
        calendar.focus()
        await pilot.press(key)
        await pilot.pause()
        assert calendar.selection.head == date(2026, 6, 11) + timedelta(days=days)


async def test_shift_and_an_arrow_grows_the_span_and_escape_takes_it_back() -> None:
    """Shift grows the span backwards too, and escape leaves the head where it is.

    The keys are bound in all four directions but the screen only ever drives
    one of them, and a `shift+up` that moved the cursor instead of extending
    would silently throw away the span somebody was building. Escape then has to
    collapse to the head rather than the anchor: it puts you where you were
    driving, not back where you set off.
    """
    calendar = YearCalendar()
    async with mounted(calendar) as pilot:
        calendar.show(JUNE, JULY_END, {}, today=JUNE)
        calendar.go_to(date(2026, 6, 18))
        calendar.focus()

        await pilot.press("shift+up", "shift+left")
        await pilot.pause()
        assert calendar.selection.anchor == date(2026, 6, 18)
        assert len(calendar.selection) == 9

        await pilot.press("escape")
        await pilot.pause()
        assert calendar.selection == Selection.at(date(2026, 6, 10))


async def test_escape_stands_down_when_there_is_nothing_to_collapse() -> None:
    """The screen binds escape too, to leave.

    A focused widget is asked first, so a calendar that always handled it would
    trap somebody on the leave screen with no way out but the mouse.
    """
    async with shown() as calendar:
        assert calendar.check_action("collapse", ()) is False
        assert calendar.check_action("move", (1,)) is True
        calendar.action_extend(1)
        assert calendar.check_action("collapse", ()) is True


async def test_stepping_a_month_from_the_31st_lands_on_a_shorter_months_last_day() -> (
    None
):
    """A month step is clamped to the length of the month it lands in.

    `[` and `]` are how somebody crosses a year, and a key that silently did
    nothing on the 31st would read as a stuck terminal.
    """
    async with shown() as calendar:
        calendar.go_to(date(2026, 1, 31))
        calendar.action_month(1)
        assert calendar.selection.head == date(2026, 2, 28)


async def test_stepping_back_from_january_lands_in_the_december_before_it() -> None:
    """A leave year runs across the turn of the calendar year."""
    async with shown() as calendar:
        calendar.go_to(date(2026, 1, 15))
        calendar.action_month(-1)
        assert calendar.selection.head == date(2025, 12, 15)


async def test_home_and_end_go_to_the_ends_of_the_year_on_show() -> None:
    """Home and end go to the ends of what is drawn.

    A leave year starts mid-month but the grid draws whole months, so the ends
    are the ends of the blocks — which is what the cursor can actually reach.
    """
    async with shown(start=date(2026, 6, 15)) as calendar:
        calendar.action_first()
        assert calendar.selection.head == JUNE
        calendar.action_last()
        assert calendar.selection.head == JULY_END


async def test_the_ends_of_a_calendar_with_no_year_in_it_are_nowhere() -> None:
    """A calendar with no year in it has no ends to go to.

    The widget is mounted before the screen has any ledgers to give it, and
    `end` on an empty grid must not reach into an empty list of blocks.
    """
    calendar = YearCalendar()
    async with mounted(calendar):
        where = calendar.selection
        calendar.action_first()
        calendar.action_last()
        assert calendar.selection == where


async def test_a_move_is_announced_and_a_quiet_one_is_not() -> None:
    """A move is announced, and a quiet one is not.

    The rail beside the grid spells out what is booked on the selection and
    redraws on nothing but this message, so a move that told nobody would leave
    the panel describing the day before. A screen restoring a cursor it saved is
    not a move somebody made, which is what `notify` is for.
    """
    calendar = YearCalendar()
    async with mounted(calendar) as pilot:
        calendar.show(JUNE, JULY_END, {})
        calendar.set_selection(Selection.at(date(2026, 6, 11)), notify=False)
        await pilot.pause()
        assert posted(pilot) == []

        calendar.set_selection(Selection.at(date(2026, 6, 12)))
        await pilot.pause()
        assert [type(message) for message in posted(pilot)] == [
            YearCalendar.SelectionChanged
        ]
        moved = posted(pilot)[0]
        assert isinstance(moved, YearCalendar.SelectionChanged)
        assert moved.selection.head == date(2026, 6, 12)


# -- the mouse ---------------------------------------------------------------


async def test_clicking_a_day_puts_the_cursor_on_it() -> None:
    """Clicking a day puts the cursor on it.

    The keys are the fast path, but a calendar you cannot click is a calendar
    that looks broken.
    """
    async with shown() as calendar:
        june = calendar.row_of(JUNE)
        assert june is not None
        click_at(calendar, x=1, y=june)
        assert calendar.selection.head == JUNE

        friday = date(2026, 6, 5)
        line = calendar.row_of(friday)
        assert line is not None
        click_at(calendar, x=sum(calendar.columns[:4]) + 1, y=line)
        assert calendar.selection.head == friday


async def test_the_mouse_reaches_the_grid_at_all() -> None:
    """A real mouse click reaches the grid.

    Everything else about clicking is asserted against the handler directly;
    this is the one that says Textual routes a click to it at all.
    """
    calendar = YearCalendar()
    async with mounted(calendar) as pilot:
        calendar.show(JUNE, JULY_END, {}, today=JUNE)
        await pilot.pause()
        await pilot.click(YearCalendar, offset=(1, 2))
        await pilot.pause()
        assert calendar.selection.head == JUNE


async def test_clicking_past_the_last_column_lands_on_the_last_day_of_the_week() -> (
    None
):
    """A click past the last column lands on the last day of the week.

    The columns are widened to fill the panel, so the far edge of the grid is
    the far edge of Sunday — and a click there that fell through would make the
    last column of the year the one place the mouse does nothing.
    """
    async with shown() as calendar:
        line = calendar.row_of(date(2026, 6, 7))
        assert line is not None
        click_at(calendar, x=calendar.grid_width + 5, y=line)
        assert calendar.selection.head == date(2026, 6, 7)


@pytest.mark.parametrize(
    ("x", "row"),
    [
        pytest.param(1, 0, id="the weekday headings"),
        pytest.param(1, 1, id="a month title"),
    ],
)
async def test_clicking_a_heading_leaves_the_cursor_where_it_was(
    x: int, row: int
) -> None:
    """A heading is a seam, not a day.

    Landing the cursor on the 1st because somebody clicked the word "June" would
    aim the next booking at a day they never chose.
    """
    async with shown() as calendar:
        calendar.go_to(date(2026, 6, 11))
        click_at(calendar, x=x, y=row)
        assert calendar.selection.head == date(2026, 6, 11)


async def test_clicking_below_the_last_month_leaves_the_cursor_where_it_was() -> None:
    """The panel is taller than the year in it once the year is nearly over."""
    async with shown() as calendar:
        calendar.go_to(date(2026, 6, 11))
        click_at(calendar, x=1, y=calendar.virtual_size.height + 2)
        assert calendar.selection.head == date(2026, 6, 11)


async def test_clicking_a_blank_cell_at_a_seam_leaves_the_cursor_where_it_was() -> None:
    """The two cells before July starts belong to no date at all."""
    async with shown() as calendar:
        calendar.go_to(date(2026, 6, 11))
        line = calendar.row_of(date(2026, 7, 1))
        assert line is not None
        click_at(calendar, x=1, y=line)
        assert calendar.selection.head == date(2026, 6, 11)


async def test_an_event_carrying_no_position_moves_nothing() -> None:
    """An event carrying no position moves nothing.

    `on_click` is handed whatever Textual routes to it, and a click is not the
    only thing that can arrive.
    """
    async with shown() as calendar:
        calendar.go_to(date(2026, 6, 11))
        calendar.on_click(SimpleNamespace())
        assert calendar.selection.head == date(2026, 6, 11)


# -- the legend --------------------------------------------------------------


def test_the_legend_names_a_key_for_every_way_to_book() -> None:
    """The legend names a key for every way to book.

    The key strip carries seven entries and this screen has eleven actions, so
    the rest live here, where somebody deciding what to book is already looking.
    The keys are read off the configured hotkeys rather than restated beside
    them, so a rebound key cannot leave the legend describing the old one.
    """
    text = legend().plain
    for kind in AbsenceType:
        assert f"{CONFIG.hotkeys.book(kind)} {kind.token}" in text
    assert "x remove" in text
    assert "half" in text


def test_the_legend_explains_the_glyphs_the_grid_draws() -> None:
    """The legend explains the glyphs the grid draws.

    Under nine columns a tile is a number and one of these, and the shapes are
    the only report of a half day on the grid itself.
    """
    text = legend().plain
    assert f"{MORNING}{AFTERNOON}" in text
    assert SPLIT in text


# -- the heading stands over the dates -------------------------------------


@pytest.mark.parametrize("width", [28, 35, 42, 49, 56, 63, 70, 84, 98, 119])
async def test_a_weekday_initial_stands_over_its_own_column_of_dates(
    width: int,
) -> None:
    """Two grids laid over each other is what the mismatch looked like.

    A date is right-aligned near the left of its tile, so a label can follow it
    on the wide form. The heading centred its initial in the whole cell, which
    put every letter three or four columns to the right of the dates it named —
    at a year's height, unmistakably wrong and hard to say why.

    Swept across widths because the remainder is spread a column at a time, so
    the seven columns are not all the same and an alignment that holds for one
    can fail for its neighbour.
    """
    async with shown(width=width) as calendar:
        heading = text_of(calendar, 0)
        dates = next(
            text_of(calendar, index)
            for index in range(1, 8)
            if len(re.findall(r"\b\d\d\b", text_of(calendar, index))) >= DAYS_IN_WEEK
        )

        initials = [match.start() for match in re.finditer(r"\S", heading)]
        units = [match.end() - 1 for match in re.finditer(r"\b\d\d\b", dates)]

        assert len(initials) == DAYS_IN_WEEK, heading
        assert initials == units, f"\n{heading!r}\n{dates!r}"


@pytest.mark.parametrize("width", [28, 35, 42, 56, 70, 84, 119])
async def test_every_line_reaches_the_same_right_edge(width: int) -> None:
    """A ragged margin down a scrolling year, and a strip that lies about itself.

    The month seam was ruled to `grid_width - 1` while the strip still declared
    `grid_width`, so each heading stopped a column short of the weeks beneath it
    and reported a length it did not have.
    """
    async with shown(width=width) as calendar:
        for line in range(min(12, calendar.virtual_size.height)):
            strip = calendar.render_line(line)

            assert strip.cell_length == calendar.grid_width, f"line {line}"
            assert len(strip.text) == calendar.grid_width, (
                f"line {line} declares {strip.cell_length} and draws "
                f"{len(strip.text)}: {strip.text!r}"
            )
