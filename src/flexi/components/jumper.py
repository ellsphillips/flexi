"""Jump mode: one keystroke puts a badge on every jumpable region.

The overlay is a modal screen reading the live compositor geometry underneath
it, so no widget carries a hook of its own.

Targets come from the current screen, not an application-wide dict, so a target
can only name a mounted widget.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import NamedTuple, Protocol, runtime_checkable

from textual.errors import NoWidget
from textual.geometry import Offset
from textual.widget import Widget

from flexi.messages import Scope

__all__ = (
    "BadgeShape",
    "HasFocusTarget",
    "HasJumpOverlays",
    "HasJumpTargets",
    "JumpInfo",
    "JumpScreen",
    "Jumpable",
    "Jumper",
    "Refreshable",
)


class JumpScreen(Protocol):
    """The geometry operations jump mode needs from a Textual screen."""

    def walk_children(self, filter_type: type[Widget]) -> list[Widget]: ...

    def get_offset(self, widget: Widget) -> Offset: ...


@runtime_checkable
class Jumpable(Protocol):
    """A widget that carries its own jump key."""

    jump_key: str


@runtime_checkable
class Refreshable(Protocol):
    """A screen that can redraw itself when the data underneath it moves."""

    def refresh_modules(self, scope: Scope) -> None: ...


@runtime_checkable
class HasJumpTargets(Protocol):
    """A screen that says which of its regions a key can reach."""

    def jump_targets(self) -> Mapping[str, str]: ...


@runtime_checkable
class HasJumpOverlays(Protocol):
    """A screen with targets that are not widgets, such as table rows."""

    def jump_overlays(self) -> dict[Offset, JumpInfo]: ...


@runtime_checkable
class HasFocusTarget(Protocol):
    """A widget that redirects a jump to a descendant.

    A module whose content is a table takes focus on the table, not the panel.
    """

    def focus_target(self) -> Widget: ...


class BadgeShape(StrEnum):
    """How a target's badge is drawn, according to what it marks."""

    CORNER = "corner"
    """A panel: the badge is a box hung on its top-left corner."""

    ROW = "row"
    """A line of a table: the badge is a chip one row tall.

    Rows sit a single cell apart, so a box would overlap its neighbours.
    """


class JumpInfo(NamedTuple):
    """One jump target: the key that reaches it, and what it reaches."""

    key: str
    """The key which should trigger the jump."""

    widget: str | Widget
    """Either the id of the target or a direct reference to it."""

    shape: BadgeShape = BadgeShape.CORNER
    """How to draw the badge."""


class Jumper:
    """The set of jump targets on one screen, resolved to screen coordinates."""

    def __init__(
        self,
        ids_to_keys: Mapping[str, str],
        screen: JumpScreen,
        extra: Callable[[], dict[Offset, JumpInfo]] | None = None,
    ) -> None:
        self.ids_to_keys = dict(ids_to_keys)
        self.screen = screen
        self.extra = extra
        """Targets that are not widgets.

        A table row has no id and no rectangle, so walking the DOM cannot reach
        it; a screen supplies the offsets itself.
        """

    def get_overlays(self) -> dict[Offset, JumpInfo]:
        """Return every visible target, keyed by where its badge belongs.

        Offsets are unique because two targets cannot occupy one cell. A widget
        the layout is hiding raises ``NoWidget`` and drops out of the map.
        """
        overlays: dict[Offset, JumpInfo] = {}
        for child in self.screen.walk_children(Widget):
            try:
                offset = self.screen.get_offset(child)
            except NoWidget:
                continue

            if child.id and child.id in self.ids_to_keys:
                overlays[offset] = JumpInfo(self.ids_to_keys[child.id], child.id)
            elif isinstance(child, Jumpable):
                overlays[offset] = JumpInfo(child.jump_key, child)

        if self.extra is not None:
            overlays.update(self.extra())
        return overlays
