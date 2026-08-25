"""The openable table, one behaviour at a time.

The records module drives this against six weeks of seeded work, which is the
right instrument for "space opens Thursday" and the wrong one for "where does
the cursor go when the row it was sitting on has been deleted". Everything here
runs a bare table in an otherwise empty app, because the row keys and the cursor
are bookkeeping: the cases worth pinning are the ones a seeded week never
produces, and each of them costs a hundredth of a second here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import PurePath
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.message import Message
from textual.pilot import Pilot

from flexi.components.expandable import (
    ABSENCE,
    DAY,
    SESSION,
    TOTAL,
    ExpandableTable,
    Row,
    RowGroup,
    day_key,
)
from flexi.theme import THEME_NAME, THEME_PATH, flexi_theme


def keys_of(rows: Sequence[Row]) -> list[str]:
    """The keys of a run of rows, in order.

    A helper for reading assertions. It lived on the module it reads until
    nothing in the application turned out to be calling it.
    """
    return [row.key for row in rows]


PACKAGE = THEME_PATH.parent.parent

MONDAY = "2026-06-08"
TUESDAY = "2026-06-09"
WEDNESDAY = "2026-06-10"


class Harness(App[None]):
    """One table, the real palette, and an inbox for what the table posts."""

    CSS_PATH: ClassVar[list[str | PurePath]] = [
        PACKAGE / "theme" / "flexi.tcss",
        PACKAGE / "styles" / "dashboard.tcss",
    ]

    def __init__(self, table: ExpandableTable) -> None:
        super().__init__()
        self.register_theme(flexi_theme())
        self.theme = THEME_NAME
        self.table = table
        self.posted: list[Message] = []

    def compose(self) -> ComposeResult:
        yield self.table

    def on_expandable_table_expanded(self, message: ExpandableTable.Expanded) -> None:
        self.posted.append(message)

    def on_expandable_table_row_selected(
        self, message: ExpandableTable.RowSelected
    ) -> None:
        self.posted.append(message)


@asynccontextmanager
async def mounted(table: ExpandableTable) -> AsyncIterator[Pilot[None]]:
    async with Harness(table).run_test(size=(60, 20)) as pilot:
        yield pilot


def posted(pilot: Pilot[None]) -> list[Message]:
    """What the table has told the screen above it since it was mounted."""
    app = pilot.app
    assert isinstance(app, Harness)
    return app.posted


def day(iso: str, *sessions: str) -> RowGroup:
    """A day, with one session row hidden behind it per `sessions`."""
    return RowGroup(
        Row(day_key(iso), (iso,)),
        tuple(
            Row(f"{SESSION}{iso}-{index}", (text,))
            for index, text in enumerate(sessions)
        ),
    )


async def table_of(pilot: Pilot[None], *groups: RowGroup) -> ExpandableTable:
    app = pilot.app
    assert isinstance(app, Harness)
    app.table.set_columns(("Day", 12))
    app.table.set_groups(groups)
    await pilot.pause()
    return app.table


# -- the row keys ------------------------------------------------------------


def test_a_row_says_what_kind_of_row_it_is_in_its_key() -> None:
    """The prefix is the only record of what a row is.

    Everything downstream — which rows a jump badge may land on, whether `x`
    deletes a session or refuses — reads the kind back off the key, so a day key
    that stopped starting with `d-` would silently make every day undeletable.
    """
    assert Row(day_key(MONDAY), ()).kind == DAY
    assert Row(f"{SESSION}12", ()).kind == SESSION
    assert Row(f"{ABSENCE}12", ()).kind == ABSENCE
    assert Row(f"{TOTAL}week", ()).kind == TOTAL
    assert day_key(MONDAY) == f"d-{MONDAY}"


def test_a_row_with_nothing_behind_it_is_not_expandable() -> None:
    """A day nobody worked still gets a row, and space on it must do nothing."""
    assert not RowGroup(Row(day_key(MONDAY), ())).expandable
    assert day(MONDAY, "09:00 – 17:00").expandable


def test_the_keys_of_a_run_of_rows_come_back_in_order() -> None:
    """Jump badges are handed out top to bottom, so the order is the meaning."""
    group = day(MONDAY, "morning", "afternoon")
    assert keys_of([group.parent, *group.children]) == [
        f"d-{MONDAY}",
        f"s-{MONDAY}-0",
        f"s-{MONDAY}-1",
    ]


# -- the header --------------------------------------------------------------


async def test_a_column_given_a_width_keeps_it_whatever_the_cells_hold() -> None:
    """Content-sized columns let the widest cell win.

    In a records table the widest cell is the punch strip, which then pushes the
    figures off the right edge on exactly the terminals where they matter most.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        table.set_columns(("Day", 7), ("strip", 20), "Worked")
        table.set_groups([RowGroup(Row(day_key(MONDAY), ("Mon", "▁▂▃", "7:24")))])
        await pilot.pause()
        widths = [column.width for column in table.ordered_columns]
        assert widths[:2] == [7, 20]
        labels = [str(column.label) for column in table.ordered_columns]
        assert labels == ["Day", "", "Worked"]


async def test_replacing_the_header_takes_the_old_rows_with_it() -> None:
    """Replacing the header clears the body under it.

    A column set kept over a redraw would leave cells sitting under headings
    that no longer describe them.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY))
        table.set_columns(("Date", 9), ("Hours", 6))
        await pilot.pause()
        assert table.row_count == 0
        assert [str(column.label) for column in table.ordered_columns] == [
            "Date",
            "Hours",
        ]


# -- opening and closing -----------------------------------------------------


async def test_children_stay_hidden_until_their_parent_is_opened() -> None:
    """The table is a month of days first and a list of sessions second."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 12:30"), day(TUESDAY))
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}", f"d-{TUESDAY}"]

        table.toggle(day_key(MONDAY))
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [
            f"d-{MONDAY}",
            f"s-{MONDAY}-0",
            f"d-{TUESDAY}",
        ]
        assert table.row_count == 3


async def test_space_inside_an_open_day_closes_the_day() -> None:
    """Toggling a child toggles its parent, and leaves the cursor on the header.

    Pressing space on a session and being told nothing happened would send
    somebody scrolling back up to the day row to close what they just opened.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 12:30", "13:00 – 17:00"))
        table.toggle(day_key(MONDAY))
        await pilot.pause()

        table.focus_key(f"s-{MONDAY}-1")
        assert table.toggle() is False
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}"]
        assert table.cursor_key == day_key(MONDAY)


async def test_opening_a_row_the_cursor_is_not_on_leaves_the_cursor_where_it_was() -> (
    None
):
    """Only a collapse the cursor is inside has nowhere else to put it.

    Dragging the cursor onto every row that happens to be toggled would lose
    somebody's place each time the screen expanded a day for them.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"), day(TUESDAY, "all day"))
        table.focus_key(day_key(TUESDAY))

        assert table.toggle(day_key(MONDAY)) is True
        await pilot.pause()
        assert table.cursor_key == day_key(TUESDAY)


async def test_a_day_with_nothing_behind_it_refuses_to_open() -> None:
    """Space on a day with no sessions leaves the table exactly as it was.

    Opening it would insert nothing and then post a message saying it had, and
    the screen redraws on that message.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY))
        assert table.toggle(day_key(MONDAY)) is False
        await pilot.pause()
        assert table.row_count == 1
        assert posted(pilot) == []


async def test_space_on_an_empty_table_does_nothing_at_all() -> None:
    """A month with no records in it is still a table somebody can press keys at."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot)
        assert table.toggle() is False
        await pilot.pause()
        assert posted(pilot) == []


async def test_pressing_space_opens_the_row_under_the_cursor() -> None:
    """The binding is what the footer advertises; the method is only its guts."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"))
        table.focus()
        await pilot.press("space")
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}", f"s-{MONDAY}-0"]
        opened = posted(pilot)[0]
        assert isinstance(opened, ExpandableTable.Expanded)
        assert (opened.key, opened.expanded) == (day_key(MONDAY), True)


async def test_expand_all_does_whichever_of_the_two_is_visible() -> None:
    """One key that always does the visible thing beats two nobody remembers."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"), day(TUESDAY, "all day"))
        table.action_toggle_all()
        await pilot.pause()
        assert table.row_count == 4

        table.action_toggle_all()
        await pilot.pause()
        assert table.row_count == 2


async def test_expand_all_can_be_told_which_way_to_go() -> None:
    """A rebuild reopens what was open, and must not invert it on the way."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"), day(TUESDAY))
        table.expand_all(expanded=True)
        await pilot.pause()
        assert table.expanded == {day_key(MONDAY)}

        table.expand_all(expanded=False)
        await pilot.pause()
        assert table.expanded == set()


async def test_the_groups_a_table_holds_are_readable_back_off_it() -> None:
    """The run of groups is readable back off the table.

    The records module counts what it loaded to decide how many jump badges it
    can hand out, and the table is the only place that run is kept.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        groups = (day(MONDAY, "09:00 – 17:00"), day(TUESDAY))
        await table_of(pilot, *groups)
        assert table.groups == groups


# -- the cursor --------------------------------------------------------------


async def test_the_cursor_stays_on_its_own_row_when_the_table_is_rebuilt() -> None:
    """The cursor is restored by key, not by index.

    Restoring by index would move the cursor every time a day above it was
    expanded, which is the redraw that happens most.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning"), day(TUESDAY), day(WEDNESDAY))
        table.focus_key(day_key(WEDNESDAY))
        table.toggle(day_key(MONDAY))
        await pilot.pause()
        assert table.cursor_key == day_key(WEDNESDAY)


async def test_a_cursor_whose_row_has_gone_falls_to_the_bottom() -> None:
    """A row usually disappears because it was just deleted.

    The eye is already at the bottom of what is left, so that is where the
    cursor belongs rather than back at the top of the month.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY), day(TUESDAY), day(WEDNESDAY))
        table.focus_key(day_key(WEDNESDAY))
        table.set_groups([day(MONDAY), day(TUESDAY)])
        await pilot.pause()
        assert table.cursor_key == day_key(TUESDAY)


async def test_deleting_the_last_record_leaves_the_cursor_nowhere_to_go() -> None:
    """An emptied table has no last row for the cursor to fall to.

    The fallback reaches for `row_count - 1`, which on nothing at all is -1 and
    would put the cursor off the top of a table with nothing in it.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY))
        table.focus_key(day_key(MONDAY))
        table.set_groups([])
        await pilot.pause()
        assert table.row_count == 0
        assert table.cursor_key is None


async def test_a_row_added_without_a_key_reports_no_key_rather_than_the_word() -> None:
    """A row with no key of its own reports none.

    `str(None)` is `"None"`, which is a perfectly good key to hand back and look
    up and never find: the caller would go looking for a row nobody named.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        table.set_columns(("Day", 12))
        table.add_row("Monday")
        await pilot.pause()
        assert table.cursor_key is None


async def test_rows_arriving_before_the_columns_do_leave_the_cursor_unread() -> None:
    """Rows with no columns to sit in leave the cursor unreadable, not raising.

    A module that fills its table before `on_mount` has set the header lands
    here, and the answer has to be "nothing under the cursor" rather than an
    exception thrown out of whatever redraw happened to ask.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        table.set_groups([RowGroup(Row(day_key(MONDAY), ()))])
        await pilot.pause()
        assert table.row_count == 1
        assert table.cursor_key is None


async def test_jumping_to_a_row_that_is_not_drawn_leaves_the_cursor_alone() -> None:
    """A badge can name a session inside a day that has since been closed."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning"), day(TUESDAY))
        table.focus_key(day_key(TUESDAY))
        table.focus_key(f"s-{MONDAY}-0")
        await pilot.pause()
        assert table.cursor_key == day_key(TUESDAY)


async def test_a_key_belonging_to_no_group_belongs_to_nothing() -> None:
    """A key naming no group answers with none.

    `x` and `enter` both ask which group the cursor is in, and a total row is in
    none of them.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning"))
        assert table.group_for(f"{TOTAL}week") is None
        assert table.group_for(f"s-{MONDAY}-0") is table.groups[0]


# -- enter -------------------------------------------------------------------


async def test_enter_opens_the_day_it_names() -> None:
    """Enter means "show me this".

    A day that answered it by staying shut would have refused the only thing the
    key means.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"))
        table.focus_key(day_key(MONDAY))
        table.action_open_row()
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}", f"s-{MONDAY}-0"]
        assert [type(message) for message in posted(pilot)] == [
            ExpandableTable.Expanded,
            ExpandableTable.RowSelected,
        ]
        selected = posted(pilot)[-1]
        assert isinstance(selected, ExpandableTable.RowSelected)
        assert selected.key == day_key(MONDAY)


async def test_enter_on_a_day_already_open_names_it_without_closing_it() -> None:
    """Enter never closes anything.

    Twice reading as open-then-shut would make it a second toggle, and space is
    already the toggle.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"))
        table.toggle(day_key(MONDAY))
        table.focus_key(day_key(MONDAY))
        table.action_open_row()
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}", f"s-{MONDAY}-0"]
        assert [type(message) for message in posted(pilot)][-1] is (
            ExpandableTable.RowSelected
        )


async def test_enter_on_a_row_with_nothing_behind_it_still_names_the_row() -> None:
    """A row with nothing behind it is still a row enter can name.

    The screen opens the editor on whatever enter names, and a day with no
    sessions on it is exactly the day somebody presses enter on to add one.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY))
        table.focus_key(day_key(MONDAY))
        table.action_open_row()
        await pilot.pause()
        assert [type(message) for message in posted(pilot)] == [
            ExpandableTable.RowSelected
        ]


async def test_enter_on_an_empty_table_names_nothing() -> None:
    """A month with no records in it still takes keys.

    A `RowSelected` carrying no row would send the screen looking for one.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot)
        table.action_open_row()
        await pilot.pause()
        assert posted(pilot) == []


async def test_replacing_the_rows_forgets_expansions_of_rows_that_have_gone() -> None:
    """`expanded` has to answer "is any row open", not "was one, ever".

    The widget outlives its rows -- the records table calls `set_groups` on
    every redraw -- so a key left in the set from a period nobody is looking at
    made `expand_all` close an already-closed table instead of opening it, for
    the rest of the session.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning"))
        table.toggle(day_key(MONDAY))
        await pilot.pause()
        assert table.expanded == {day_key(MONDAY)}

        await table_of(pilot, day(WEDNESDAY, "morning"))

        assert table.expanded == set(), "a row that is gone cannot be open"
