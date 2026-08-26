"""The records table: one row per day, opening to the day's breakdown.

A collapsed row is a whole day in one line; opening it shows the sessions and
breaks behind the figures, so the table answers both "how was the week" and "why
is Thursday short" without a second screen.

Strips are painted into cells rather than mounted: thirty-one widgets would cost
a layout pass per redraw, on the one widget that redraws every second.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.geometry import Offset
from textual.message import Message
from textual.widgets import Static

from flexi.components.expandable import (
    ABSENCE,
    DAY,
    SESSION,
    TOTAL,
    ExpandableTable,
    Row,
    RowGroup,
)
from flexi.components.jumper import JumpInfo
from flexi.components.modules.base import Module
from flexi.components.punch import PUNCH_CLASSES, render_strip
from flexi.config import CONFIG
from flexi.constants import DayKind
from flexi.domain.format import clock, delta, hm
from flexi.domain.ledger import DayLedger
from flexi.domain.period import Granularity
from flexi.domain.punch import cell_count
from flexi.messages import Scope

COLUMNS: tuple[tuple[str, int] | str, ...] = (
    ("Day", 7),
    ("strip", 36),
    ("Worked", 7),
    ("±", 6),
)
STRIP_WIDTH_FLOOR = 12
FIXED_COLUMNS = 7 + 7 + 6
CELL_PADDING = 8
"""Two columns of padding on each of the four cells — DataTable's own default."""
MAX_JUMP_ROWS = 9

BRANCH = "├"
LAST = "└"

BADGE_WIDTH = 3
"""A jump badge is one character with a column of padding either side.

The row badges sit against the table's right edge rather than its left. A badge
over the left edge covers the day name, which is the one thing on the row you
need in order to choose which badge to press."""


class BookHere(Message):
    """Book an absence on the day under the cursor. Handled by the screen."""

    def __init__(self, iso: str | None) -> None:
        super().__init__()
        self.iso = iso


class DeleteHere(Message):
    """Delete whatever the cursor is on — an absence, or a session."""

    def __init__(self, key: str | None) -> None:
        super().__init__()
        self.key = key


class RecordsModule(Module):
    """Every day in the period, expandable."""

    WATCHES: ClassVar[Scope] = Scope.ALL

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        *PUNCH_CLASSES,
        "record--sub",
        "record--total",
        "record--absent",
        "record--holiday",
        "record--surplus",
        "record--deficit",
        "record--muted",
    }

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(CONFIG.hotkeys.book_absence, "book_here", "Book absence", show=True),
        Binding(CONFIG.hotkeys.delete, "delete_here", "Delete", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(id="records-module", title="Records", **kwargs)
        self._strip_width = 24

    def compose(self) -> ComposeResult:
        yield ExpandableTable(id="records-table", zebra_stripes=False)
        yield Static(
            "No days in this period", id="records-empty", classes="empty-indicator"
        )

    def on_mount(self) -> None:
        self.query_one("#records-table", ExpandableTable).set_columns(*COLUMNS)
        self.rebuild()

    def on_resize(self) -> None:
        """Re-measure the strip column after the table has been laid out.

        The other three columns are fixed, so the strip takes what remains —
        which is what makes it a shared time axis across the rows rather than a
        per-row bar. Deferred, because the module is resized before its table is.
        """
        self.call_after_refresh(self._remeasure)

    def _remeasure(self) -> None:
        width = self._available_strip_width()
        if width == self._strip_width:
            return
        self._strip_width = width
        # Size the column to the strip it will actually hold. The bucket sizes
        # are fixed — 15 minutes means something, 13.7 does not — so a strip
        # rarely fills its budget exactly, and an auto-sized column would leave
        # the remainder as a gap between the graphic and the figures beside it.
        table = self.query_one("#records-table", ExpandableTable)
        for key, column in table.columns.items():
            if str(key.value) == "strip":
                column.width = cell_count(self.services.ledger.window, width)
        self.rebuild()

    def _available_strip_width(self) -> int:
        table = self.query_one("#records-table", ExpandableTable)
        outer = table.size.width or max(0, self.size.width - 4)
        return max(STRIP_WIDTH_FLOOR, outer - FIXED_COLUMNS - CELL_PADDING - 1)

    # -- drawing -----------------------------------------------------------

    def rebuild(self) -> None:
        period = self.period
        ledgers = self.services.ledger.days(period.start, period.end, now=self.now)
        window = self.services.ledger.window
        table = self.query_one("#records-table", ExpandableTable)

        groups = [self._group(ledger, window) for ledger in ledgers]
        groups.append(self._total_group(ledgers))
        table.set_groups(groups)

        empty = self.query_one("#records-empty", Static)
        empty.display = not ledgers
        table.display = bool(ledgers)
        self.set_subtitle(_totals_subtitle(ledgers))

    def _group(self, ledger: DayLedger, window: object) -> RowGroup:
        parent = Row(
            key=f"{DAY}{ledger.date.isoformat()}",
            cells=(
                self._day_cell(ledger),
                render_strip(
                    ledger,
                    self._strip_width,
                    window,  # type: ignore[arg-type]
                    self.get_component_rich_style,
                    self.now,
                ),
                self._worked_cell(ledger),
                self._delta_cell(ledger),
            ),
        )
        return RowGroup(parent, self._children(ledger))

    def _children(self, ledger: DayLedger) -> tuple[Row, ...]:
        """The day's breakdown: absences, sessions, breaks, and the arithmetic.

        A day with nothing recorded has no children and therefore does not open,
        which is what stops `space` feeling broken on an empty week.
        """
        rows: list[Row] = []
        sub = self.get_component_rich_style("record--sub")

        for slice_ in ledger.absences:
            detail = f"{slice_.label} — {slice_.note}" if slice_.note else slice_.label
            rows.append(
                Row(
                    key=f"{ABSENCE}{slice_.absence_id}",
                    cells=(
                        Text("", style=sub),
                        Text(f"  {BRANCH} {detail}", style=sub),
                        Text("—", style=sub, justify="right"),
                        Text("", style=sub),
                    ),
                )
            )

        ordered = sorted(ledger.segments, key=lambda item: item.start)
        for index, segment in enumerate(ordered):
            finish = "open" if segment.is_open else clock(segment.finish(self.now))
            note = segment.note or ("auto-closed" if segment.auto_closed else "worked")
            rows.append(
                Row(
                    key=f"{SESSION}{segment.session_id}",
                    cells=(
                        Text("", style=sub),
                        Text(
                            f"  {BRANCH} {clock(segment.start)} → {finish}  {note}",
                            style=sub,
                        ),
                        Text(
                            hm(segment.duration(self.now)), style=sub, justify="right"
                        ),
                        Text("", style=sub),
                    ),
                )
            )
            if index + 1 < len(ordered) and segment.end is not None:
                gap = ordered[index + 1].start - segment.end
                if gap > timedelta():
                    rows.append(
                        Row(
                            key=f"{SESSION}{segment.session_id}-break",
                            cells=(
                                Text("", style=sub),
                                Text(f"  {BRANCH} break", style=sub),
                                Text(hm(gap), style=sub, justify="right"),
                                Text("", style=sub),
                            ),
                        )
                    )

        if rows or ledger.expected:
            total = self.get_component_rich_style("record--total")
            rows.append(
                Row(
                    key=f"{TOTAL}{ledger.date.isoformat()}",
                    cells=(
                        Text("", style=total),
                        Text(f"  {LAST} expected", style=total),
                        Text(hm(ledger.expected), style=total, justify="right"),
                        self._delta_cell(ledger),
                    ),
                )
            )
        return tuple(rows)

    def _total_group(self, ledgers: list[DayLedger]) -> RowGroup:
        """The period's own line, under a rule."""
        style = self.get_component_rich_style("record--total")
        worked = sum((item.worked for item in ledgers), start=timedelta())
        expected = sum((item.expected for item in ledgers), start=timedelta())
        toil = sum((item.toil_taken for item in ledgers), start=timedelta())
        label = (
            "Day"
            if self.period.granularity is Granularity.DAY
            else self.period.granularity.label
        )
        return RowGroup(
            Row(
                key=f"{TOTAL}period",
                cells=(
                    Text(label, style=style),
                    Text("", style=style),
                    Text(hm(worked), style=style, justify="right"),
                    self._signed(worked - expected - toil),
                ),
            )
        )

    # -- cells -------------------------------------------------------------

    def _day_cell(self, ledger: DayLedger) -> Text:
        name = ledger.date.strftime("%a %d")
        if ledger.kind is DayKind.HOLIDAY:
            return Text(name, style=self.get_component_rich_style("record--holiday"))
        if ledger.kind is DayKind.ABSENT:
            return Text(name, style=self.get_component_rich_style("record--absent"))
        if ledger.kind is DayKind.UNTRACKED or not ledger.is_working_day:
            return Text(name, style=self.get_component_rich_style("record--muted"))
        return Text(name)

    def _worked_cell(self, ledger: DayLedger) -> Text:
        if ledger.is_holiday or (ledger.absences and not ledger.segments):
            return Text(
                ledger.summary,
                style=self.get_component_rich_style("record--muted"),
                justify="right",
            )
        if not ledger.segments:
            return Text(
                "—",
                style=self.get_component_rich_style("record--muted"),
                justify="right",
            )
        return Text(hm(ledger.worked), justify="right")

    def _delta_cell(self, ledger: DayLedger) -> Text:
        if not ledger.expected and not ledger.worked:
            return Text("")
        return self._signed(ledger.delta)

    def _signed(self, value: timedelta) -> Text:
        if value > timedelta():
            style = "record--surplus"
        elif value < timedelta():
            style = "record--deficit"
        else:
            style = "record--muted"
        return Text(
            delta(value), style=self.get_component_rich_style(style), justify="right"
        )

    # -- interaction -------------------------------------------------------

    def focus_target(self) -> ExpandableTable:
        """Jumps land on the rows, not on the panel around them."""
        return self.table

    @property
    def table(self) -> ExpandableTable:
        """The table itself, for the screen's jump targets and actions."""
        return self.query_one("#records-table", ExpandableTable)

    def jump_row_targets(self) -> dict[Offset, JumpInfo]:
        """A number key over each of the first nine visible day rows.

        A row is not a widget, so the offsets come from the table's own geometry rather
        than from walking the DOM, and a row scrolled out of view is not offered.
        """
        table = self.table
        region = table.region
        if not region.area:
            return {}
        header = table.header_height if table.show_header else 0
        scroll = int(table.scroll_offset.y)
        targets: dict[Offset, JumpInfo] = {}
        numbered = 0
        for index, row in enumerate(table.visible_rows()):
            if row.kind != DAY:
                continue
            numbered += 1
            if numbered > MAX_JUMP_ROWS:
                break
            y = region.y + header + index - scroll
            if not (region.y + header <= y < region.y + region.height):
                continue
            targets[Offset(region.x + region.width - BADGE_WIDTH, y)] = JumpInfo(
                str(numbered), row.key
            )
        return targets

    def selected_date(self) -> str | None:
        """The ISO date of the day under the cursor, whatever row it is on."""
        key = self.table.cursor_key
        if key is None:
            return None
        group = self.table.group_for(key)
        # Unreachable: `cursor_key` can only name a row the table holds, and
        # `set_groups` is the only thing that puts rows in it, so every key it
        # returns belongs to a group.
        if group is None:  # pragma: no cover
            return None
        parent = group.parent.key
        return parent[len(DAY) :] if parent.startswith(DAY) else None

    def action_book_here(self) -> None:
        self.post_message(BookHere(self.selected_date()))

    def action_delete_here(self) -> None:
        self.post_message(DeleteHere(self.table.cursor_key))


def _totals_subtitle(ledgers: list[DayLedger]) -> str:
    """Worked against expected for the whole period, in the module's live slot."""
    worked = sum((item.worked for item in ledgers), start=timedelta())
    expected = sum((item.expected for item in ledgers), start=timedelta())
    return f"{hm(worked)} of {hm(expected)}"
