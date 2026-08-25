"""Jump mode: one keystroke puts a badge on every jumpable region.

Nothing needs a per-widget hook -- the overlay is a modal screen that reads the
live compositor geometry underneath it.

Targets are asked of the current screen rather than held in an application-wide
dict, so a target can only ever name something that is mounted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, NamedTuple, Protocol, runtime_checkable

from textual.errors import NoWidget
from textual.geometry import Offset
from textual.screen import Screen
from textual.widget import Widget


@runtime_checkable
class Jumpable(Protocol):
    """A widget that names its own jump key rather than being registered."""

    jump_key: str


@runtime_checkable
class HasJumpTargets(Protocol):
    """A screen that says which of its regions a key can reach."""

    def jump_targets(self) -> Mapping[str, str]: ...


@runtime_checkable
class HasJumpOverlays(Protocol):
    """A screen with targets that are not widgets -- table rows, say."""

    def jump_overlays(self) -> dict[Offset, JumpInfo]: ...


@runtime_checkable
class HasFocusTarget(Protocol):
    """A widget that would rather the jump landed somewhere inside it.

    A module whose content is a table wants the table: landing on the panel and
    needing a second key to get into the rows is the friction jump mode exists
    to remove.
    """

    def focus_target(self) -> Widget: ...


class JumpInfo(NamedTuple):
    """One jump target: the key that reaches it, and what it reaches."""

    key: str
    """The key which should trigger the jump."""

    widget: str | Widget
    """Either the id of the target or a direct reference to it."""


class Jumper:
    """The set of jump targets on one screen, resolved to screen coordinates."""

    def __init__(
        self,
        ids_to_keys: Mapping[str, str],
        screen: Screen[Any],
        extra: Callable[[], dict[Offset, JumpInfo]] | None = None,
    ) -> None:
        self.ids_to_keys = dict(ids_to_keys)
        self.screen = screen
        self.extra = extra
        """Targets that are not widgets.

        A table row has no id and no rectangle of its own, so it cannot be found
        by walking the DOM. A screen that wants rows to be jumpable computes
        their screen offsets itself and hands them over here."""

    def get_overlays(self) -> dict[Offset, JumpInfo]:
        """Every visible target, keyed by where its badge belongs.

        Keyed by offset rather than by id because two targets cannot occupy the
        same cell, and because the overlay needs the position anyway. A widget
        the layout is currently hiding raises ``NoWidget`` and is skipped, which
        is how a collapsed or off-screen panel drops out of the map without the
        caller having to know it might.
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
