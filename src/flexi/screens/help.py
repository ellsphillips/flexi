"""Every binding, grouped, including the ones the key strip had to drop.

Built from the *live* bindings of the screen underneath rather than from a
hand-written table, so a key that exists is listed and a key that was renamed
cannot go stale here. The grouping is by the widget that owns the binding, which
is also the answer to "why did that key do nothing" — a binding on the records
table is only live when the records table has focus.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from flexi.components.common import KeyHint, Rule


class HelpScreen(ModalScreen[None]):
    """The keyboard, written down."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_help", "Close", show=True),
        Binding("question_mark", "dismiss_help", "Close", show=False),
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


def collect_bindings(screen: object) -> dict[str, list[tuple[str, str]]]:
    """Group a screen's active bindings by the widget that declared them.

    Flexi's own only. Textual gives every scrollable container eight bindings of its
    own, and listing Scroll Up and Page Left turns a keyboard reference into a list
    of things nobody came here to learn.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    seen: set[tuple[str, str]] = set()
    active = getattr(screen, "active_bindings", {})
    app = getattr(screen, "app", None)
    for node, binding, _enabled, _tooltip in active.values():
        if not binding.description or not declared_by_flexi(node):
            continue
        owner = label_for(node)
        marker = (owner, binding.action)
        if marker in seen:
            continue
        seen.add(marker)
        display = app.get_key_display(binding) if app else binding.key
        groups.setdefault(owner, []).append((display, binding.description))
    return groups


def declared_by_flexi(node: object) -> bool:
    """True when the binding was declared by Flexi rather than by Textual."""
    return type(node).__module__.startswith("flexi.")


def label_for(node: object) -> str:
    """A human name for the widget a binding belongs to."""
    name = type(node).__name__
    return {
        "FlexiApp": "Anywhere",
        "DashboardScreen": "Dashboard",
        "InsightsScreen": "Insights",
        "SettingsScreen": "Settings",
        "RecordsModule": "Records",
        "ExpandableTable": "Records table",
        "WalletModule": "Wallet",
        "MonthView": "Calendar",
        "ClockModule": "Clock",
    }.get(name, name)
