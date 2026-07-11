"""Jump mode: spatial navigation by one keystroke.

Adapted from the reference application, which took the idea from the Amp editor. Press the jump
key, every jumpable region grows a one-key badge over its top-left corner, press
that key and focus lands there.

The mechanism is worth understanding because it looks like it should need
per-widget hooks and does not: the overlay is a modal screen, and it reads the
*live compositor geometry* of the screen underneath it. A widget is jumpable
because the screen said so, not because it implements anything.

Flexi's one departure from the reference application: targets are asked for per screen rather than
kept in a single application-wide dict. the reference application lists every container id in the
whole application in ``App.on_mount``, including ids belonging to screens that
are not mounted, and a target that misses is silently dropped. Asking the live
screen means a target can only ever name something that is there.
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
        self.keys_to_ids = {key: widget_id for widget_id, key in ids_to_keys.items()}
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
