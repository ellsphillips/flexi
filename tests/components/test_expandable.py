"""The openable table, one behaviour at a time.

Everything here runs a bare table in an otherwise empty app. The row keys and
the cursor are bookkeeping, and the cases worth pinning are the ones the
records module's seeded weeks never produce.
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
    ExpandableTable,
    Row,
    RowGroup,
    RowKind,
    row_key,
)
from flexi.theme import THEME_NAME, THEME_PATH, flexi_theme


def keys_of(rows: Sequence[Row]) -> list[str]:
    """The keys of a run of rows, in order."""
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
        Row(row_key(RowKind.DAY, iso), (iso,)),
        tuple(
            Row(f"{RowKind.SESSION}{iso}-{index}", (text,))
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


# ---- the row keys ----


def test_row_kind_comes_from_its_key() -> None:
    """The prefix is the only record of what a row is."""
    assert Row(row_key(RowKind.DAY, MONDAY), ()).kind == RowKind.DAY
    assert Row(f"{RowKind.SESSION}12", ()).kind == RowKind.SESSION
    assert Row(f"{RowKind.ABSENCE}12", ()).kind == RowKind.ABSENCE
    assert Row(f"{RowKind.TOTAL}week", ()).kind == RowKind.TOTAL
    assert row_key(RowKind.DAY, MONDAY) == f"d-{MONDAY}"


def test_row_with_no_children_is_not_expandable() -> None:
    """An unworked day still gets a row, and space on it must do nothing."""
    assert not RowGroup(Row(row_key(RowKind.DAY, MONDAY), ())).expandable
    assert day(MONDAY, "09:00 – 17:00").expandable


def test_row_keys_come_back_in_order() -> None:
    """Jump badges are handed out top to bottom, so the order is the meaning."""
    group = day(MONDAY, "morning", "afternoon")
    assert keys_of([group.parent, *group.children]) == [
        f"d-{MONDAY}",
        f"s-{MONDAY}-0",
        f"s-{MONDAY}-1",
    ]


# ---- the header ----


async def test_fixed_column_width_survives_wide_cells() -> None:
    """Content-sized columns let the widest cell, the punch strip, win."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        table.set_columns(("Day", 7), ("strip", 20), "Worked")
        table.set_groups(
            [RowGroup(Row(row_key(RowKind.DAY, MONDAY), ("Mon", "▁▂▃", "7:24")))]
        )
        await pilot.pause()
        widths = [column.width for column in table.ordered_columns]
        assert widths[:2] == [7, 20]
        labels = [str(column.label) for column in table.ordered_columns]
        assert labels == ["Day", "", "Worked"]


async def test_replacing_the_header_clears_the_rows() -> None:
    """Cells kept over a header change sit under headings that no longer fit."""
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


# ---- opening and closing ----


async def test_children_stay_hidden_until_opened() -> None:
    """The table is a month of days first and a list of sessions second."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 12:30"), day(TUESDAY))
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}", f"d-{TUESDAY}"]

        table.toggle(row_key(RowKind.DAY, MONDAY))
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [
            f"d-{MONDAY}",
            f"s-{MONDAY}-0",
            f"d-{TUESDAY}",
        ]
        assert table.row_count == 3


async def test_space_on_a_child_closes_the_day() -> None:
    """Toggling a child toggles its parent, and leaves the cursor on the header."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 12:30", "13:00 – 17:00"))
        table.toggle(row_key(RowKind.DAY, MONDAY))
        await pilot.pause()

        table.focus_key(f"s-{MONDAY}-1")
        assert table.toggle() is False
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}"]
        assert table.cursor_key == row_key(RowKind.DAY, MONDAY)


async def test_opening_another_row_keeps_the_cursor() -> None:
    """Only a collapse the cursor is inside has nowhere else to put it."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"), day(TUESDAY, "all day"))
        table.focus_key(row_key(RowKind.DAY, TUESDAY))

        assert table.toggle(row_key(RowKind.DAY, MONDAY)) is True
        await pilot.pause()
        assert table.cursor_key == row_key(RowKind.DAY, TUESDAY)


async def test_day_with_no_sessions_refuses_to_open() -> None:
    """Opening would insert nothing and post a message the screen redraws on."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY))
        assert table.toggle(row_key(RowKind.DAY, MONDAY)) is False
        await pilot.pause()
        assert table.row_count == 1
        assert posted(pilot) == []


async def test_space_on_an_empty_table_does_nothing() -> None:
    """A month with no records is still a table that takes keys."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot)
        assert table.toggle() is False
        await pilot.pause()
        assert posted(pilot) == []


async def test_space_opens_the_row_under_the_cursor() -> None:
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
        assert (opened.key, opened.expanded) == (row_key(RowKind.DAY, MONDAY), True)


async def test_expand_all_toggles_both_ways() -> None:
    """One key that always does the visible thing, in place of two."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"), day(TUESDAY, "all day"))
        table.action_toggle_all()
        await pilot.pause()
        assert table.row_count == 4

        table.action_toggle_all()
        await pilot.pause()
        assert table.row_count == 2


async def test_expand_all_takes_a_direction() -> None:
    """A rebuild reopens what was open, and must not invert it on the way."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"), day(TUESDAY))
        table.expand_all(expanded=True)
        await pilot.pause()
        assert table.expanded == {row_key(RowKind.DAY, MONDAY)}

        table.expand_all(expanded=False)
        await pilot.pause()
        assert table.expanded == set()


async def test_table_groups_are_readable_back() -> None:
    """The table is the only place the run of groups is kept."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        groups = (day(MONDAY, "09:00 – 17:00"), day(TUESDAY))
        await table_of(pilot, *groups)
        assert table.groups == groups


# ---- the cursor ----


async def test_cursor_is_restored_by_key() -> None:
    """Restoring by index would move the cursor whenever a day above it opened."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning"), day(TUESDAY), day(WEDNESDAY))
        table.focus_key(row_key(RowKind.DAY, WEDNESDAY))
        table.toggle(row_key(RowKind.DAY, MONDAY))
        await pilot.pause()
        assert table.cursor_key == row_key(RowKind.DAY, WEDNESDAY)


async def test_cursor_falls_to_the_last_row() -> None:
    """A row usually disappears because it was just deleted."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY), day(TUESDAY), day(WEDNESDAY))
        table.focus_key(row_key(RowKind.DAY, WEDNESDAY))
        table.set_groups([day(MONDAY), day(TUESDAY)])
        await pilot.pause()
        assert table.cursor_key == row_key(RowKind.DAY, TUESDAY)


async def test_emptied_table_has_no_cursor_key() -> None:
    """The fallback reaches for `row_count - 1`, which on an empty table is -1."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY))
        table.focus_key(row_key(RowKind.DAY, MONDAY))
        table.set_groups([])
        await pilot.pause()
        assert table.row_count == 0
        assert table.cursor_key is None


async def test_keyless_row_reports_no_cursor_key() -> None:
    """`str(None)` is `"None"`, a key that looks up cleanly and finds no row."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        table.set_columns(("Day", 12))
        table.add_row("Monday")
        await pilot.pause()
        assert table.cursor_key is None


async def test_rows_before_columns_leave_no_cursor_key() -> None:
    """A module filling its table before `on_mount` sets the header lands here."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        table.set_groups([RowGroup(Row(row_key(RowKind.DAY, MONDAY), ()))])
        await pilot.pause()
        assert table.row_count == 1
        assert table.cursor_key is None


async def test_focusing_a_hidden_row_does_nothing() -> None:
    """A badge can name a session inside a day that has since been closed."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning"), day(TUESDAY))
        table.focus_key(row_key(RowKind.DAY, TUESDAY))
        table.focus_key(f"s-{MONDAY}-0")
        await pilot.pause()
        assert table.cursor_key == row_key(RowKind.DAY, TUESDAY)


async def test_key_in_no_group_returns_none() -> None:
    """`x` and `enter` ask which group the cursor is in; a total row is in none."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning"))
        assert table.group_for(f"{RowKind.TOTAL}week") is None
        assert table.group_for(f"s-{MONDAY}-0") is table.groups[0]


# ---- enter ----


async def test_enter_opens_and_moves_into_the_day() -> None:
    """Enter means "show me this", and then puts the cursor in it."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"))
        table.focus_key(row_key(RowKind.DAY, MONDAY))
        table.action_open_row()
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}", f"s-{MONDAY}-0"]
        assert table.cursor_key == f"s-{MONDAY}-0"


async def test_enter_keeps_an_open_day_open() -> None:
    """Enter never closes anything; space is the toggle."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "09:00 – 17:00"))
        table.toggle(row_key(RowKind.DAY, MONDAY))
        table.focus_key(row_key(RowKind.DAY, MONDAY))
        table.action_open_row()
        await pilot.pause()
        assert keys_of(table.visible_rows()) == [f"d-{MONDAY}", f"s-{MONDAY}-0"]
        assert table.cursor_key == f"s-{MONDAY}-0"


async def test_enter_on_a_child_does_not_close() -> None:
    """A guard reading the child's own expanded state finds it never set."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning", "afternoon"))
        table.toggle(row_key(RowKind.DAY, MONDAY))
        table.focus_key(f"s-{MONDAY}-1")
        table.action_open_row()
        await pilot.pause()
        assert table.expanded == {row_key(RowKind.DAY, MONDAY)}
        assert table.cursor_key == f"s-{MONDAY}-0"


async def test_enter_on_an_empty_day_stays_put() -> None:
    """A day with nothing recorded has nothing to drop into."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY))
        table.focus_key(row_key(RowKind.DAY, MONDAY))
        table.action_open_row()
        await pilot.pause()
        assert table.cursor_key == row_key(RowKind.DAY, MONDAY)
        assert posted(pilot) == []


async def test_enter_on_an_empty_table_opens_nothing() -> None:
    """A month with no records still takes keys."""
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot)
        table.action_open_row()
        await pilot.pause()
        assert posted(pilot) == []


async def test_set_groups_forgets_stale_expansions() -> None:
    """`expanded` has to answer "is any row open", not "was one, ever".

    The widget outlives its rows: the records table calls `set_groups` on every
    redraw, and a stale key makes `expand_all` close an already-closed table.
    """
    table = ExpandableTable()
    async with mounted(table) as pilot:
        await table_of(pilot, day(MONDAY, "morning"))
        table.toggle(row_key(RowKind.DAY, MONDAY))
        await pilot.pause()
        assert table.expanded == {row_key(RowKind.DAY, MONDAY)}

        await table_of(pilot, day(WEDNESDAY, "morning"))

        assert table.expanded == set(), "a row that is gone cannot be open"
