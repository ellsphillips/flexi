"""The modal that draws the jump badges.

Dismisses with the id of, or a reference to, the widget the user chose — or
``None`` when they pressed escape, in which case the app puts focus back exactly
where it was. That restoration is the whole reason jump mode feels safe to try:
a mode you can leave without consequence is one people will press by accident and
keep using on purpose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Label

if TYPE_CHECKING:
    from flexi.components.jumper import Jumper

SWALLOWED_KEYS: frozenset[str] = frozenset({"tab", "shift+tab"})
"""Keys stopped rather than let through.

If these reach the parent after the overlay closes, the parent handles them and
focus shifts again — unexpectedly, and after the jump target was already
focused, which reads as the jump having gone to the wrong place.
"""


class JumpOverlay(ModalScreen[str | Widget | None]):
    """The badges, and the two bars that explain the mode."""

    HELP_LABEL = "Jump mode"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_overlay", "Dismiss", show=False),
    ]

    def __init__(
        self,
        jumper: Jumper,
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
            label = Label(jump_info.key, classes="textual-jump-label")
            label.styles.offset = offset
            yield label
        with Center(id="textual-jump-info"):
            yield Label("Press a key to jump")
        with Center(id="textual-jump-dismiss"):
            yield Label("esc to dismiss")
