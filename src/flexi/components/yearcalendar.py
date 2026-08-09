"""A scrolling year of days, with a cursor you can book leave on.

The dashboard's calendar is a date picker: one month, paged, three cells wide.
This is the other thing a calendar can be — a continuous surface you move over
and act on, where a fortnight in August is visible as a fortnight and booking it
costs one keystroke.

It draws itself with the Line API rather than mounting a widget per day. A leave
year is 365 days and about 60 rows; 365 widgets would cost a layout pass every
time the cursor moved, and the cursor moves on every arrow key.

Colour carries the *type* of a booking and the glyph carries its *portion*, so
the two never compete for the same cell — the same rule the year heatmap
follows. Nothing is ever colour alone: the selection panel beside this spells
out what is booked on the day under the cursor.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import ClassVar, Final

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.binding import Binding, BindingType
from textual.geometry import Region, Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from flexi.constants import Portion
from flexi.domain.ledger import DayLedger
from flexi.domain.stitch import (
    DAYS_IN_WEEK,
    MonthBlock,
    Selection,
    stitch,
    weekday_initials,
)

TOKEN: Final = 3
"""What a day always occupies: two columns for the number, one for the marker."""

LABELLED_CELL: Final = 9
"""From here a tile has room to say what is booked on it, not just that
something is. Below it the type is carried by the tile's colour alone — which is
why the panel beside the grid always spells the selected day out."""

MIN_CELL: Final = 4
"""Two columns for the number, one for the marker, one of gutter.

There is no maximum. An earlier draft capped the cell and centred the grid in
the leftover, which left slabs of unpainted panel down both sides and read as a
rendering fault. A day is a *tile* instead: it takes an equal share of the full
width and paints its own ground, so there is no leftover to look wrong.
"""

FULL: Final = "●"
MORNING: Final = "◐"
AFTERNOON: Final = "◑"
SPLIT: Final = "◆"
HOLIDAY: Final = "·"
BLANK: Final = " "

PORTION_GLYPH: Final[dict[Portion, str]] = {
    Portion.FULL: FULL,
    Portion.AM: MORNING,
    Portion.PM: AFTERNOON,
}


class YearCalendar(ScrollView, can_focus=True):
    """Months stitched into one scrolling grid, with a movable selection."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "cal--month",
        "cal--weekday",
        "cal--day",
        "cal--empty",
        "cal--outside",
        "cal--weekend",
        "cal--today",
        "cal--cursor",
        "cal--selected",
        "cal--holiday",
        "cal--annual",
        "cal--sick",
        "cal--toil",
        "cal--unpaid",
        "cal--other",
    }

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left,h", "move(-1)", "Back a day", show=False),
        Binding("right,l", "move(1)", "On a day", show=False),
        Binding("up,k", "move(-7)", "Back a week", show=False),
        Binding("down,j", "move(7)", "On a week", show=False),
        Binding("shift+left", "extend(-1)", "Extend", show=False),
        Binding("shift+right", "extend(1)", "Extend", show=False),
        Binding("shift+up", "extend(-7)", "Extend", show=False),
        Binding("shift+down", "extend(7)", "Extend", show=False),
        Binding("escape", "collapse", "One day", show=False),
        Binding("left_square_bracket", "month(-1)", "Previous month", show=False),
        Binding("right_square_bracket", "month(1)", "Next month", show=False),
        Binding("home", "first", "Start", show=False),
        Binding("end", "last", "End", show=False),
    ]

    class SelectionChanged(Message):
        """The cursor moved, or the selection grew."""

        def __init__(self, selection: Selection) -> None:
            super().__init__()
            self.selection = selection

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.blocks: tuple[MonthBlock, ...] = ()
        self.ledgers: dict[date, DayLedger] = {}
        self.selection = Selection.at(date.today())
        self.first_weekday = 0
        self._today = date.today()
        self._rows: list[tuple[MonthBlock | None, int]] = []
        """One entry per drawn line: the block it belongs to, and which of its
        rows it is. ``-1`` is a month title, ``-2`` a weekday heading."""

    @property
    def _available(self) -> int:
        return max(DAYS_IN_WEEK * MIN_CELL, self.content_size.width or self.size.width)

    @property
    def columns(self) -> tuple[int, ...]:
        """The width of each of the seven columns.

        The remainder is spread one column at a time rather than dumped on the
        last, so the grid fills the panel exactly and no column is more than a
        cell wider than its neighbour.
        """
        base, extra = divmod(self._available, DAYS_IN_WEEK)
        return tuple(
            base + (1 if index < extra else 0) for index in range(DAYS_IN_WEEK)
        )

    @property
    def cell(self) -> int:
        """The narrowest column, for anything that has to fit in all of them."""
        return min(self.columns)

    @property
    def grid_width(self) -> int:
        return sum(self.columns)

    def on_resize(self) -> None:
        """The grid is sized to the panel, so a resize re-lays it out."""
        self._relayout()
        self.refresh()

    # -- content -----------------------------------------------------------

    def show(
        self,
        start: date,
        end: date,
        ledgers: dict[date, DayLedger],
        *,
        today: date | None = None,
        first_weekday: int = 0,
    ) -> None:
        """Lay out a span and draw what is booked on it."""
        self.first_weekday = first_weekday
        self.blocks = tuple(stitch(start, end, first_weekday=first_weekday))
        self.ledgers = ledgers
        self._today = today or date.today()
        self._relayout()
        self.refresh()

    def _relayout(self) -> None:
        rows: list[tuple[MonthBlock | None, int]] = [(None, -2)]
        for block in self.blocks:
            rows.append((block, -1))
            rows.extend((block, index) for index in range(len(block.rows)))
        self._rows = rows
        self.virtual_size = Size(self.grid_width, len(rows))

    # -- the selection -----------------------------------------------------

    def set_selection(self, selection: Selection, *, notify: bool = True) -> None:
        self.selection = selection
        self.scroll_to_day(selection.head)
        self.refresh()
        if notify:
            self.post_message(self.SelectionChanged(selection))

    def action_move(self, days: int) -> None:
        self.set_selection(self.selection.move(days))

    def action_extend(self, days: int) -> None:
        self.set_selection(self.selection.extend(days))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Stand `escape` down when there is nothing to collapse.

        The screen also binds `escape`, to leave. A focused widget is asked
        first, so a calendar that always handled it would trap somebody on the
        screen; returning False skips the binding and lets the key bubble.
        """
        del parameters
        if action == "collapse":
            return not self.selection.single
        return True

    def action_collapse(self) -> None:
        self.set_selection(self.selection.collapse())

    def action_month(self, offset: int) -> None:
        """A month at a time, keeping the day of the month where it can.

        Clamped to the length of the target month, so stepping from the 31st
        lands on the 30th rather than refusing to move.
        """
        head = self.selection.head
        total = head.year * 12 + head.month - 1 + offset
        year, month = total // 12, total % 12 + 1
        import calendar

        day = min(head.day, calendar.monthrange(year, month)[1])
        self.set_selection(self.selection.go_to(date(year, month, day)))

    def action_first(self) -> None:
        if self.blocks:
            self.set_selection(self.selection.go_to(self.blocks[0].first))

    def action_last(self) -> None:
        if self.blocks:
            self.set_selection(self.selection.go_to(self.blocks[-1].last))

    def go_to(self, when: date) -> None:
        self.set_selection(self.selection.go_to(when))

    # -- geometry ----------------------------------------------------------

    def row_of(self, when: date) -> int | None:
        """Which drawn line a date sits on."""
        for index, (block, row) in enumerate(self._rows):
            if block is None or row < 0 or not block.contains(when):
                continue
            if any(cell.date == when for cell in block.rows[row]):
                return index
        return None

    def month_rows(self) -> list[tuple[MonthBlock, int]]:
        """Every month title on the drawn surface, with its line."""
        return [
            (block, index)
            for index, (block, row) in enumerate(self._rows)
            if block is not None and row == -1
        ]

    def visible_months(self) -> list[tuple[MonthBlock, int]]:
        """The month titles currently on screen, for jump targets."""
        top = int(self.scroll_offset.y)
        bottom = top + self.size.height
        return [
            (block, line - top)
            for block, line in self.month_rows()
            if top <= line < bottom
        ]

    def scroll_to_day(self, when: date) -> None:
        """Keep the cursor on screen, with a row of context either side."""
        line = self.row_of(when)
        if line is None:
            return
        self.scroll_to_region(
            Region(0, max(0, line - 1), self.grid_width, 3), animate=False
        )

    # -- drawing -----------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        line = y + int(self.scroll_offset.y)
        if line >= len(self._rows):
            return Strip.blank(self.size.width)
        block, row = self._rows[line]

        if row == -2:
            return self._heading_strip()
        if block is None:
            return Strip.blank(self.size.width)
        if row == -1:
            return self._title_strip(block)
        return self._week_strip(block, row)

    def _heading_strip(self) -> Strip:
        style = self.get_component_rich_style("cal--weekday")
        initials = weekday_initials(self.first_weekday)
        text = "".join(
            initial.center(width)
            for initial, width in zip(initials, self.columns, strict=False)
        )
        return Strip([Segment(text, style)], self.grid_width)

    def _title_strip(self, block: MonthBlock) -> Strip:
        """A seam. Ruled rather than boxed, like every other divider here."""
        style = self.get_component_rich_style("cal--month")
        label = f" {block.title} "
        rule = "─" * max(0, self.grid_width - len(label) - 1)
        return Strip([Segment(f"{label}{rule}", style)], self.grid_width)

    def _week_strip(self, block: MonthBlock, row: int) -> Strip:
        empty = self.get_component_rich_style("cal--empty")
        segments: list[Segment] = []
        for cell, width in zip(block.rows[row], self.columns, strict=False):
            if cell.date is None:
                segments.append(Segment(BLANK * width, empty))
                continue
            segments.append(self._day_segment(cell.date, width))
        return Strip(segments, self.grid_width)

    def _day_segment(self, when: date, width: int) -> Segment:
        """A tile: the day, what is on it, and the whole cell painted.

        Every column is emitted with a style, including the blanks at a seam.
        Left unstyled they take whatever the widget's own ground is, which
        showed up as slabs of a different colour down the side of the grid.

        Given room, the tile says what is booked rather than leaving the reader
        to decode a colour. Below that it falls back to the number and a marker,
        right-aligned so the columns still read as columns.
        """
        ledger = self.ledgers.get(when)
        style = self._day_style(when, ledger)
        if width >= LABELLED_CELL:
            label = self._label(ledger)
            text = f" {when.day:>2} {label}" if label else f" {when.day:>2}"
            return Segment(text[: width - 1].ljust(width), style)
        token = f"{when.day:>2}{self._marker(ledger)}"
        return Segment(token.rjust(width - 1) + BLANK, style)

    def _label(self, ledger: DayLedger | None) -> str:
        """What is on the day, in a word."""
        if ledger is None:
            return ""
        if ledger.is_holiday:
            return "hol"
        if not ledger.absences:
            return ""
        if len(ledger.absences) > 1:
            return "part day"
        slice_ = ledger.absences[0]
        word = slice_.type.short.lower()
        return (
            word
            if slice_.portion is Portion.FULL
            else f"{word} {PORTION_GLYPH[slice_.portion]}"
        )

    def _marker(self, ledger: DayLedger | None) -> str:
        """The glyph says how much of the day; the colour says what kind."""
        if ledger is None:
            return BLANK
        if ledger.is_holiday:
            return HOLIDAY
        if not ledger.absences:
            return BLANK
        if len(ledger.absences) > 1:
            kinds = {slice_.type for slice_ in ledger.absences}
            return SPLIT if len(kinds) > 1 else FULL
        return PORTION_GLYPH[ledger.absences[0].portion]

    def _day_style(self, when: date, ledger: DayLedger | None) -> Style:
        """One style per tile, in order of what the reader needs most.

        The cursor and the selection win outright: where you are is more urgent
        than what is booked there, and the selection panel spells the booking
        out anyway. Under them, a booked day takes its type's ground and a
        bank holiday takes its own; today is underlined on top of whatever it
        landed on, because it can coincide with any of them.
        """
        if when == self.selection.head:
            return self.get_component_rich_style("cal--cursor")
        if when in self.selection:
            return self.get_component_rich_style("cal--selected")

        if ledger is None:
            base = self.get_component_rich_style("cal--outside")
        elif ledger.is_holiday:
            base = self.get_component_rich_style("cal--holiday")
        elif ledger.absences:
            base = self.get_component_rich_style(
                f"cal--{ledger.absences[0].type.token}"
            )
        elif not ledger.is_working_day:
            base = self.get_component_rich_style("cal--weekend")
        else:
            base = self.get_component_rich_style("cal--day")

        if when == self._today:
            base += self.get_component_rich_style("cal--today")
        return base

    # -- the pointer -------------------------------------------------------

    def on_click(self, event: object) -> None:
        """Move the cursor to the day that was clicked.

        The keys are the fast path, but a calendar you cannot click is a
        calendar that looks broken.
        """
        offset = getattr(event, "offset", None)
        if offset is None:
            return
        line = int(offset.y) + int(self.scroll_offset.y)
        column, edge = 0, 0
        for index, width in enumerate(self.columns):
            edge += width
            if int(offset.x) < edge:
                column = index
                break
        else:
            column = DAYS_IN_WEEK - 1
        if not (0 <= line < len(self._rows)) or not (0 <= column < DAYS_IN_WEEK):
            return
        block, row = self._rows[line]
        if block is None or row < 0:
            return
        cell = block.rows[row][column]
        if cell.date is not None:
            self.set_selection(self.selection.go_to(cell.date))


def legend() -> Text:
    """What the keys do, and what the glyphs mean.

    The key strip carries seven entries and this screen has eleven direct
    actions, so the rest live here — where somebody deciding what to book is
    already looking.
    """
    text = Text(no_wrap=False, end="")
    for row in (
        [("A", "annual"), ("S", "sick"), ("T", "toil")],
        [("U", "unpaid"), ("O", "other"), ("x", "remove")],
        [("␣", "half"), ("e", "edit"), ("g", "go to")],
    ):
        for index, (key, what) in enumerate(row):
            if index:
                text.append("  ")
            text.append(key, "reverse")
            text.append(f" {what}")
        text.append("\n")
    text.append(f"{MORNING}{AFTERNOON} half day   {SPLIT} split")
    return text


def days_between(start: date, end: date) -> list[date]:
    """Every date in a span, inclusive."""
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]
