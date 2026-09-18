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
from textual.screen import ModalScreen, Screen
from textual.widgets import Static

from flexi.components.common import KeyHint, Rule

__all__ = ("HelpScreen", "collect_bindings", "declared_by_flexi", "label_for")


class HelpScreen(ModalScreen[None]):
    """The keyboard, written down."""

    HELP_LABEL = "Help"

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


def collect_bindings(screen: Screen[object]) -> dict[str, list[tuple[str, str]]]:
    """Group a screen's active bindings by the widget that declared them.

    Flexi's own only. Textual gives every scrollable container eight bindings of its
    own, and listing Scroll Up and Page Left turns a keyboard reference into a list
    of things nobody came here to learn.

    One row per action, carrying every key that runs it: `left,h` is one Binding
    in the source and two in `active_bindings`, and keeping the first of them
    left the vim keys off the only page that lists the keyboard.
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
    """True when this key was declared by Flexi rather than inherited from Textual.

    Asked of the binding rather than of the widget holding it. A Flexi table is
    still a `DataTable`, so a filter on the widget's own module let Cursor Left
    and Page Right through on the strength of the subclass they were inherited
    into.
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

    Read off the class rather than looked up in a table here. The table had no
    entry for the leave screen or its calendar, so the help modal filed their
    eleven keys under `LeaveScreen` and `YearCalendar` -- and nothing said so,
    because a missing entry falls back to the class name and a class name is a
    string like any other.
    `tests/test_layering.py::test_every_class_with_keys_says_what_to_call_it`
    refuses a Flexi class that declares bindings and no `HELP_LABEL`.
    """
    return str(getattr(type(node), "HELP_LABEL", type(node).__name__))
