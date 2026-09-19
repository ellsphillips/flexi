"""A table whose rows open to show what is inside them.

Row keys are typed by prefix (``d-`` a day, ``s-`` a session, ``a-`` an absence,
``t-`` a total), so a key says what it is and no parallel bookkeeping can fall
out of step with the table. The cursor is restored by key, not by index:
expanding a row above it moves its index.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Unpack

from rich.console import RenderableType
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import DataTable
from textual.widgets.data_table import CellDoesNotExist, RowDoesNotExist

from flexi.components.options import DataTableOptions
from flexi.config import CONFIG

__all__ = (
    "ExpandableTable",
    "Row",
    "RowGroup",
    "RowKind",
    "row_ident",
    "row_key",
)


class RowKind(StrEnum):
    """What a row is, carried in its own key."""

    DAY = "d-"
    SESSION = "s-"
    ABSENCE = "a-"
    TOTAL = "t-"


def row_key(kind: RowKind, ident: object) -> str:
    """A row key: what the row is, and which one (`d-2026-06-11`, `a-7`)."""
    return f"{kind.value}{ident}"


def row_ident(kind: RowKind, key: str) -> str | None:
    """What a key of that kind names, or ``None`` when it is another kind.

    `row_ident(RowKind.ABSENCE, "a-7")` is `"7"`; asked for a `DAY`, the same
    key is `None`.
    """
    return key[len(kind.value) :] if key.startswith(kind.value) else None


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the table."""

    key: str
    cells: tuple[RenderableType, ...]

    @property
    def kind(self) -> str:
        """The two-character prefix that says what this row is."""
        return self.key[:2]


@dataclass(frozen=True, slots=True)
class RowGroup:
    """A row, and the rows it hides until it is opened."""

    parent: Row
    children: tuple[Row, ...] = field(default_factory=tuple)

    @property
    def expandable(self) -> bool:
        """True when there is something behind this row worth opening."""
        return bool(self.children)


class ExpandableTable(DataTable[RenderableType]):
    """A ``DataTable`` with openable rows."""

    HELP_LABEL = "Records table"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(CONFIG.hotkeys.expand, "toggle_row", "Expand", show=True),
        Binding(CONFIG.hotkeys.expand_all, "toggle_all", "Expand all", show=False),
        Binding("enter", "open_row", "Open", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("home", "scroll_top", "First", show=False),
        Binding("end", "scroll_bottom", "Last", show=False),
    ]

    class Expanded(Message):
        """A row was opened or closed."""

        def __init__(self, key: str, *, expanded: bool) -> None:
            super().__init__()
            self.key = key
            self.expanded = expanded

    def __init__(self, **kwargs: Unpack[DataTableOptions]) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self._expanded: set[str] = set()
        self._groups: tuple[RowGroup, ...] = ()

    # --- content ----------------------------------------------------------

    def set_columns(self, *specs: str | tuple[str, int]) -> None:
        """Replace the header. Clears the body, which the caller then refills.

        A spec may carry a width. ``DataTable`` sizes an unwidthed column to its
        widest cell, which in a records table is the punch strip, and the figures
        go off the right edge.
        """
        self.clear(columns=True)
        for spec in specs:
            if isinstance(spec, tuple):
                label, width = spec
                # A column can be keyed and still be headless: `strip` is a name
                # for the code, not a word for the reader.
                self.add_column(
                    "" if label == "strip" else label, width=width, key=label
                )
            else:
                self.add_column(spec, key=spec or None)

    def set_groups(self, groups: Iterable[RowGroup]) -> None:
        """Replace every row, keeping the cursor on whatever it was on.

        Expansions are kept for the rows that survive and dropped for the rest.
        The widget outlives its rows: the records table is rebuilt on every
        redraw, so `expanded` must answer "is any row open" and not "has any
        row ever been open".
        """
        self._groups = tuple(groups)
        self._expanded &= {group.parent.key for group in self._groups}
        self._redraw()

    @property
    def groups(self) -> tuple[RowGroup, ...]:
        """The groups currently loaded, expanded or not."""
        return self._groups

    @property
    def expanded(self) -> frozenset[str]:
        """The keys of the rows currently open. Read-only, like `groups`."""
        return frozenset(self._expanded)

    def visible_rows(self) -> list[Row]:
        """Every row that would be drawn, parents and opened children, in order."""
        rows: list[Row] = []
        for group in self._groups:
            rows.append(group.parent)
            if group.parent.key in self._expanded:
                rows.extend(group.children)
        return rows

    def _redraw(self) -> None:
        remembered = self.cursor_key
        # Read before `clear()`: `DataTable.clear` resets `cursor_coordinate`
        # to (0, 0).
        was_at = self.cursor_row
        self.clear()
        for row in self.visible_rows():
            self.add_row(*row.cells, key=row.key)
        self._restore_cursor(remembered, was_at)

    def _restore_cursor(self, key: str | None, was_at: int = 0) -> None:
        """Put the cursor back on the row it was on, by key.

        When the remembered row has gone, falls back to ``was_at`` and then to
        the last row. ``was_at`` is passed in because the table has been cleared
        by the time this runs, and clearing moves the cursor home.
        """
        if key is None:
            return
        try:
            self.move_cursor(row=self.get_row_index(key))
        except RowDoesNotExist:
            if self.row_count:
                self.move_cursor(row=min(was_at, self.row_count - 1))

    # --- cursor -----------------------------------------------------------

    @property
    def cursor_key(self) -> str | None:
        """The key of the row under the cursor, or ``None`` on an empty table."""
        if not self.row_count:
            return None
        try:
            key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key
        except CellDoesNotExist:
            return None
        return None if key.value is None else str(key.value)

    def focus_key(self, key: str) -> None:
        """Put the cursor on a row by key, if it is visible."""
        try:
            self.move_cursor(row=self.get_row_index(key))
        except RowDoesNotExist:
            return

    # --- expansion --------------------------------------------------------

    def group_for(self, key: str) -> RowGroup | None:
        """The group a key belongs to, whether the key is a parent or a child."""
        for group in self._groups:
            if group.parent.key == key or any(
                child.key == key for child in group.children
            ):
                return group
        return None

    def toggle(self, key: str | None = None) -> bool:
        """Open or close a row. Returns whether it ended up open.

        A key naming a child toggles that child's parent, so pressing space
        anywhere inside an open day closes it.
        """
        group = self.group_for(key) if key is not None else self._group_at_cursor()
        if group is None or not group.expandable:
            return False
        parent = group.parent.key
        # Collapsing a group the cursor is in leaves it nowhere to sit, so the
        # cursor moves to the parent. Toggling any other row leaves it alone.
        cursor_inside = self._group_at_cursor() is group
        expanded = parent not in self._expanded
        self._expanded.symmetric_difference_update({parent})
        self._redraw()
        if cursor_inside:
            self.focus_key(parent)
        self.post_message(self.Expanded(parent, expanded=expanded))
        return expanded

    def expand_all(self, *, expanded: bool | None = None) -> None:
        """Open or close every expandable row.

        With no argument: if anything is open, close everything; otherwise open
        everything.
        """
        if expanded is None:
            expanded = not self._expanded
        if expanded:
            self._expanded = {
                group.parent.key for group in self._groups if group.expandable
            }
        else:
            self._expanded.clear()
        self._redraw()

    def _group_at_cursor(self) -> RowGroup | None:
        key = self.cursor_key
        return None if key is None else self.group_for(key)

    # --- actions ----------------------------------------------------------

    def action_toggle_row(self) -> None:
        self.toggle()

    def action_toggle_all(self) -> None:
        self.expand_all()

    def action_open_row(self) -> None:
        """Open the day under the cursor and drop into it.

        Never a second toggle: space is the toggle. On a row inside an open day
        this moves to the top of that day.
        """
        group = self._group_at_cursor()
        if group is None or not group.expandable:
            return
        if group.parent.key not in self._expanded:
            self.toggle(group.parent.key)
        self.focus_key(group.children[0].key)
