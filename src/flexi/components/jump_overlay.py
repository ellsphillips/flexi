"""The modal that draws the jump badges.

Dismisses with the id of, or a reference to, the widget the user chose — or
``None`` when they pressed escape, in which case the app puts focus back exactly
where it was. That restoration is the whole reason jump mode feels safe to try:
a mode you can leave without consequence is one people will press by accident and
keep using on purpose.
"""

from __future__ import annotations

from typing import ClassVar, Protocol

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center
from textual.geometry import Offset
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Label

from flexi.components.jumper import BadgeShape, JumpInfo

__all__ = (
    "BADGE_OVERHANG",
    "SWALLOWED_KEYS",
    "JumpOverlay",
    "JumpOverlayProvider",
    "badge_offset",
)

SWALLOWED_KEYS: frozenset[str] = frozenset({"tab", "shift+tab"})
"""Keys stopped rather than let through.

If these reach the parent after the overlay closes, the parent handles them and
focus shifts again — unexpectedly, and after the jump target was already
focused, which reads as the jump having gone to the wrong place.
"""


BADGE_OVERHANG = 1
"""How far a badge sits outside the corner it marks, in cells.

The badge is a box three rows tall rather than a chip one row tall, so it is
hung on the corner instead of laid along the top border: its middle row lands
on the line, and it reads as a tag attached to that panel rather than as a
caption floating above it. One cell each way is what puts the corner behind the
badge's own corner.
"""


def badge_offset(corner: Offset, shape: BadgeShape) -> Offset:
    """Where a badge is drawn for a target whose top-left is ``corner``.

    A row chip is drawn exactly where it was placed: the caller computed a line
    of a table, and moving it would put it on a different one.

    A corner box is hung outward, and clamped at the screen edge. A panel flush
    against the top or the left has nothing to overhang into, and a negative
    offset does not move a widget outward -- it clips the border off the side
    that left the screen, so the badge would lose the corner that makes it read
    as a box.
    """
    if shape is BadgeShape.ROW:
        return corner
    return Offset(
        max(0, corner.x - BADGE_OVERHANG),
        max(0, corner.y - BADGE_OVERHANG),
    )


class JumpOverlayProvider(Protocol):
    """A collaborator that resolves the badges visible on the current screen."""

    def get_overlays(self) -> dict[Offset, JumpInfo]: ...


class JumpOverlay(ModalScreen[str | Widget | None]):
    """The badges, and the two bars that explain the mode."""

    HELP_LABEL = "Jump mode"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_overlay", "Dismiss", show=False),
    ]

    def __init__(
        self,
        jumper: JumpOverlayProvider,
        name: str | None = None,
        id: str | None = None,  # noqa: A002 - Textual's own parameter name
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.jumper = jumper
        self.keys_to_widgets: dict[str, Widget | str] = {}
        self._resize_counter = 0

    def on_key(self, key_event: events.Key) -> None:
        if key_event.key in SWALLOWED_KEYS:
            key_event.stop()
            key_event.prevent_default()

        if not self.is_active:
            return
        target = self.keys_to_widgets.get(key_event.key)
        if target is not None:
            key_event.stop()
            key_event.prevent_default()
            self.dismiss(target)

    def action_dismiss_overlay(self) -> None:
        self.dismiss(None)

    async def on_resize(self) -> None:
        """Redraw the badges when the layout under them moves.

        The first resize is the one that mounts the overlay, and recomposing
        during it would throw away the children being mounted.
        """
        self._resize_counter += 1
        if self._resize_counter == 1:
            return
        await self.recompose()

    def _sync(self) -> None:
        self.overlays = self.jumper.get_overlays()
        self.keys_to_widgets = {
            info.key: info.widget for info in self.overlays.values()
        }

    def compose(self) -> ComposeResult:
        self._sync()
        for offset, jump_info in self.overlays.items():
            label = Label(
                jump_info.key,
                classes=f"textual-jump-label -{jump_info.shape}",
            )
            label.styles.offset = badge_offset(offset, jump_info.shape)
            yield label
        with Center(id="textual-jump-info"):
            yield Label("Press a key to jump")
        with Center(id="textual-jump-dismiss"):
            yield Label("esc to dismiss")
