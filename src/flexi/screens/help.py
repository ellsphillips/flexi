"""Every binding, including the ones the key strip has no room for.

Built from the live bindings of the screen underneath, not from a hand-written
table, so the page lists the keys that exist. The grouping is by the widget that
owns the binding: a binding on the records table is live only while the records
table has focus.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Static

from flexi.components.common import KeyHint, Rule
from flexi.config import CONFIG

__all__ = ("HelpScreen", "collect_bindings", "declared_by_flexi", "label_for")


class HelpScreen(ModalScreen[None]):
    """The whole keyboard, grouped by the widget each key belongs to."""

    HELP_LABEL = "Help"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_help", "Close", show=True),
        Binding(CONFIG.hotkeys.help, "dismiss_help", "Close", show=False),
    ]

    def __init__(self, groups: dict[str, list[tuple[str, str]]]) -> None:
        super().__init__()
        self._groups = groups

    def compose(self) -> ComposeResult:
        with Container(classes="modal", id="help-modal"):
            yield Static("Keyboard", classes="modal-title")
            with VerticalScroll(id="help-body"):
                for owner, bindings in self._groups.items():
                    if not bindings:
                        continue
                    yield Rule(owner)
                    for key, description in bindings:
                        yield KeyHint(key, description)
            yield Static(
                "Anything without a key is in the command palette — ctrl+p.",
                classes="caption",
            )

    def action_dismiss_help(self) -> None:
        self.dismiss(None)


def collect_bindings(screen: Screen[object]) -> dict[str, list[tuple[str, str]]]:
    """Group a screen's active bindings by the widget that declared them.

    Flexi's own bindings only: Textual gives every scrollable container eight
    bindings of its own. One row per action, carrying every key that runs it,
    because `left,h` is one Binding in the source and two in `active_bindings`.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    rows: dict[tuple[str, str], int] = {}
    for node, binding, _enabled, _tooltip in screen.active_bindings.values():
        if not binding.description or not declared_by_flexi(node, binding):
            continue
        owner = label_for(node)
        listed = groups.setdefault(owner, [])
        display = screen.app.get_key_display(binding)
        where = rows.get((owner, binding.action))
        if where is None:
            rows[owner, binding.action] = len(listed)
            listed.append((display, binding.description))
        else:
            keys, description = listed[where]
            listed[where] = (f"{keys} / {display}", description)
    return groups


def declared_by_flexi(node: object, binding: Binding) -> bool:
    """True when Flexi declared this key, not Textual.

    Asked of the binding, not of the widget holding it: a Flexi table is still a
    `DataTable`, so a filter on the widget's own module admits Cursor Left and
    Page Right on the strength of the subclass they are inherited into.
    """
    for cls in type(node).__mro__:
        own = cls.__dict__.get("BINDINGS")
        if own is None:
            continue
        declared = {(item.key, item.action) for item in Binding.make_bindings(own)}
        if (binding.key, binding.action) in declared:
            return cls.__module__.startswith("flexi.")
    return False


def label_for(node: object) -> str:
    """The heading a binding is filed under.

    Read off the class, not looked up in a table, where a missing entry falls
    back to the class name with nothing to say it did. Enforced by
    `tests/test_layering.py`, which refuses a Flexi class that declares bindings
    and no `HELP_LABEL`.
    """
    return str(getattr(type(node), "HELP_LABEL", type(node).__name__))
