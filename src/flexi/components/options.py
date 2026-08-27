"""Typed keyword contracts for composing Flexi widgets with Textual.

Textual's constructors are deliberately explicit, but a small wrapper often
needs to forward those options while adding one piece of Flexi data.  These
contracts preserve the framework's public keyword API without turning that
forwarding boundary into ``Any``.
"""

from __future__ import annotations

from typing import Literal, TypedDict

__all__ = (
    "DataTableOptions",
    "ModuleOptions",
    "ScreenOptions",
    "StaticOptions",
    "WidgetOptions",
)


class WidgetOptions(TypedDict, total=False):
    """Keyword options accepted by :class:`textual.widget.Widget`."""

    name: str | None
    id: str | None
    classes: str | None
    disabled: bool
    markup: bool


class StaticOptions(WidgetOptions, total=False):
    """Keyword options accepted after a ``Static`` widget's content."""

    expand: bool
    shrink: bool


class ScreenOptions(TypedDict, total=False):
    """Keyword options accepted by Textual screens and modal screens."""

    name: str | None
    id: str | None
    classes: str | None


class ModuleOptions(TypedDict, total=False):
    """Options available once a dashboard module fixes its id and classes."""

    expand: bool
    shrink: bool
    markup: bool
    name: str | None
    disabled: bool


class DataTableOptions(TypedDict, total=False):
    """Keyword options accepted by :class:`textual.widgets.DataTable`."""

    show_header: bool
    show_row_labels: bool
    fixed_rows: int
    fixed_columns: int
    zebra_stripes: bool
    header_height: int
    show_cursor: bool
    cursor_foreground_priority: Literal["renderable", "css"]
    cursor_background_priority: Literal["renderable", "css"]
    cursor_type: Literal["cell", "row", "column", "none"]
    cell_padding: int
    name: str | None
    id: str | None
    classes: str | None
    disabled: bool
