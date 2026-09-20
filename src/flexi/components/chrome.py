"""The frame every screen sits in: header, navigation, status line, key strip.

:data:`NAV_ITEMS` is the one table naming which screens exist, which key reaches
them and what they are called; the bindings, the nav bar and the command palette
are all built from it. It lives here and not in ``app.py`` because the widgets
need it and the app imports the widgets.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Final

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Label, Static

import flexi
from flexi.components.common import Pill, Tone

__all__ = (
    "NAV_BY_SCREEN",
    "NAV_ITEMS",
    "OVERFLOW_TEMPLATE",
    "STATUS_WORDS",
    "AppFooter",
    "AppHeader",
    "BindingHint",
    "KeyStrip",
    "Lockup",
    "NavBar",
    "NavItem",
    "NavItemLabel",
    "OverflowLabel",
    "StatusBar",
    "StripEntry",
    "VersionTag",
    "footer_key_cost",
    "keys_that_fit",
    "stamped",
    "strip_entries",
)


def stamped(version: str) -> str:
    """A release number with the display `v` on the front.

    The packaging metadata carries no `v`: `importlib.metadata` and every
    comparison against it want the bare number.
    """
    return f"v{version}"


OVERFLOW_TEMPLATE: Final = "+{count} more"
"""A template, so the strip can price the marker at its widest, every entry
hidden, before it knows the number."""


@dataclass(frozen=True, slots=True)
class NavItem:
    """One destination: a screen, the key that jumps to it, and its name."""

    key: str
    screen: str
    label: str
    description: str


NAV_ITEMS: Final[tuple[NavItem, ...]] = (
    NavItem("f1", "dashboard", "Dashboard", "Clock, balance, wallet and records"),
    NavItem("f2", "leave", "Leave", "Book and remove leave across the year"),
    NavItem("f3", "insights", "Insights", "How the balance and the allowances moved"),
    NavItem("f4", "settings", "Settings", "Hours, leave year, bank holidays"),
)

NAV_BY_SCREEN: Final[Mapping[str, NavItem]] = MappingProxyType(
    {item.screen: item for item in NAV_ITEMS}
)


class Lockup(Horizontal):
    """`flexi` plus a teal full stop. The only fixed point in the interface.

    Not `Wordmark`: that name belongs to the ray-traced animation on the setup
    screen, in `components/wordmark.py`.
    """

    def compose(self) -> ComposeResult:
        yield Static("flexi", classes="wordmark-name")
        yield Static("·", classes="wordmark-dot")


class NavItemLabel(Static):
    """One clickable destination in the nav bar.

    A widget, not a line of markup, so it can hover and so a click lands on the
    item and need not be resolved to one by column.
    """

    def __init__(self, item: NavItem, *, active: bool = False) -> None:
        super().__init__(item.label, classes="nav-item")
        self.item = item
        self.set_class(active, "-active")
        self.tooltip = f"{item.key.upper()} · {item.description}"

    def on_click(self) -> None:
        self.post_message(NavBar.Selected(self.item))


class NavBar(Horizontal):
    """The clickable screen list, with the active destination in teal."""

    class Selected(Message):
        """A destination was clicked. Handled by the app, which does the jump."""

        def __init__(self, item: NavItem) -> None:
            super().__init__()
            self.item = item

    active: reactive[str] = reactive("dashboard", init=False)

    def compose(self) -> ComposeResult:
        # Read at compose: a widget's `on_mount` runs before its own children
        # are usable, so the first frame is already correct here and
        # `watch_active` only has to handle later changes.
        self.set_reactive(NavBar.active, str(getattr(self.app, "nav", self.active)))
        for item in NAV_ITEMS:
            yield NavItemLabel(item, active=item.screen == self.active)

    def watch_active(self) -> None:
        for label in self.query(NavItemLabel):
            label.set_class(label.item.screen == self.active, "-active")


class VersionTag(Static):
    """The installed version, and whether a newer one has been published.

    The tag is told about a newer version; running the check costs a request and
    belongs to the application. Told nothing, it says what is installed.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "version--arrow",
        "version--latest",
    }

    latest: reactive[str] = reactive("", init=False)
    """The newer published version, or empty when this is the newest."""

    def render(self) -> Text:
        installed = Text(stamped(flexi.__version__), style=self.rich_style)
        if not self.latest:
            return installed
        installed.append(" → ", style=self.get_component_rich_style("version--arrow"))
        installed.append(
            stamped(self.latest), style=self.get_component_rich_style("version--latest")
        )
        return installed

    def watch_latest(self, latest: str) -> None:
        from flexi.versioning import UPGRADE_HINT

        self.set_class(bool(latest), "-outdated")
        self.tooltip = (
            f"Version {latest} is available. {UPGRADE_HINT}" if latest else None
        )
        # With layout: the tag grows by the width of an arrow and a version, and
        # a plain refresh would redraw it inside the width it had before.
        self.refresh(layout=True)


class AppHeader(Horizontal):
    """Wordmark, navigation, and the date and period in play.

    The context is pushed to it: every screen that has one writes it in its own
    `on_mount` or `rebuild`.
    """

    context: reactive[str] = reactive("", init=False)

    def compose(self) -> ComposeResult:
        yield Lockup()
        yield NavBar()
        yield Static(self.context, classes="header-context", id="header-context")
        yield VersionTag(id="header-version", classes="header-version")

    def watch_context(self, context: str) -> None:
        if self.is_mounted:
            self.query_one("#header-context", Static).update(context)

    def set_active(self, screen: str) -> None:
        for bar in self.query(NavBar):
            bar.active = screen

    def offer_update(self, latest: str) -> None:
        """Show that a newer version has been published."""
        for tag in self.query(VersionTag):
            tag.latest = latest


STATUS_WORDS: Final[Mapping[Tone, str]] = MappingProxyType(
    {
        Tone.NEUTRAL: "",
        Tone.OK: "done",
        Tone.WARN: "warning",
        Tone.ERR: "refused",
        Tone.ACCENT: "mode",
    }
)
"""What a status pill says when its caller names no word of its own.

The message line is drawn in one colour whatever happened on it, so the tone
reaches the reader through the pill or nowhere, and a pill with no label is not
drawn at all.
"""


class StatusBar(Horizontal):
    """A transient line: what just happened, and one pill of state.

    Where every service result surfaces, as against the key hints below it,
    which say what you *can* do.
    """

    def compose(self) -> ComposeResult:
        yield Static("", classes="status-message", id="status-message")
        yield Pill("", id="status-pill")

    def set_status(
        self, message: str, tone: Tone = Tone.NEUTRAL, *, pill: str = ""
    ) -> None:
        if not self.is_mounted:
            return
        self.query_one("#status-message", Static).update(message)
        self.query_one("#status-pill", Pill).set_state(pill or STATUS_WORDS[tone], tone)


def footer_key_cost(key_display: str, description: str) -> int:
    """Columns one compact footer entry occupies, including its right margin.

    A compact :class:`BindingHint` puts one space before the description and
    the strip adds one column of right margin: two strings plus two columns.
    """
    return len(key_display) + len(description) + 2


def keys_that_fit(costs: Sequence[int], budget: int, marker: int) -> int:
    """How many entries fit in ``budget``, leaving room to say what did not.

    The last entry on the strip does not need its right margin, hence the -1 in
    both branches. When everything fits the marker costs nothing; when it does
    not, room for it is reserved before the first entry is admitted.
    """
    if sum(costs) - 1 <= budget:
        return len(costs)
    used = 0
    for count, cost in enumerate(costs):
        if used + cost + marker - 1 > budget:
            return count
        used += cost
    # Unreachable for a non-negative marker: reaching it needs
    # `sum(costs) + marker - 1 <= budget`, which the guard above rules out.
    return len(costs)  # pragma: no cover


@dataclass(frozen=True, slots=True)
class StripEntry:
    """One candidate for the key strip: what it says, and what it costs."""

    key: str
    display: str
    description: str
    action: str
    enabled: bool
    tooltip: str

    @property
    def cost(self) -> int:
        return footer_key_cost(self.display, self.description)


def strip_entries(screen: Screen[object]) -> list[StripEntry]:
    """The shown bindings of a screen, one entry per action, in declared order.

    One per *action*, not one per binding as Textual's own footer does, so one
    action bound to two keys is advertised once.
    """
    seen: dict[str, StripEntry] = {}
    for _node, binding, enabled, tooltip in screen.active_bindings.values():
        if binding.show and binding.action not in seen:
            seen[binding.action] = StripEntry(
                binding.key,
                screen.app.get_key_display(binding),
                binding.description,
                binding.action,
                enabled,
                tooltip or binding.description,
            )
    return list(seen.values())


class BindingHint(Widget):
    """One clickable key binding in the footer.

    Textual keeps the widget it draws each binding with private, so this one
    reproduces the footer's rendering and pointer behaviour.
    """

    ALLOW_SELECT: ClassVar[bool] = False
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "binding-hint--key",
        "binding-hint--description",
    }
    DEFAULT_CSS: ClassVar[str] = """
    BindingHint {
        width: auto;
        height: 1;
        text-wrap: nowrap;
        background: $footer-item-background;

        .binding-hint--key {
            color: $footer-key-foreground;
            background: $footer-key-background;
            text-style: bold;
            padding: 0 1;
        }

        .binding-hint--description {
            padding: 0 1 0 0;
            color: $footer-description-foreground;
            background: $footer-description-background;
        }

        &:hover {
            pointer: pointer;
            color: $footer-key-foreground;
            background: $block-hover-background;
        }

        &.-disabled {
            text-style: dim;
        }

        &.-compact {
            .binding-hint--key {
                padding: 0;
            }
            .binding-hint--description {
                padding: 0 0 0 1;
            }
        }
    }
    """

    compact: reactive[bool] = reactive(True, toggle_class="-compact")
    """Whether to render without padding around the key."""

    def __init__(
        self,
        key: str,
        key_display: str,
        description: str,
        action: str,
        *,
        disabled: bool = False,
        tooltip: str = "",
        classes: str = "",
    ) -> None:
        self.key = key
        self.key_display = key_display
        self.description = description
        self.action = action
        self.binding_enabled = not disabled
        if disabled:
            classes += " -disabled"
        super().__init__(classes=classes)
        self.set_reactive(Widget.shrink, False)
        if tooltip:
            self.tooltip = tooltip

    def render(self) -> Text:
        """Render the key and its description using independently styled parts."""
        key_style = self.get_component_rich_style("binding-hint--key")
        description_style = self.get_component_rich_style("binding-hint--description")
        key_padding = self.get_component_styles("binding-hint--key").padding
        description_padding = self.get_component_styles(
            "binding-hint--description"
        ).padding

        key_text = " " * key_padding.left + self.key_display + " " * key_padding.right
        if self.description:
            description_text = (
                " " * description_padding.left
                + self.description
                + " " * description_padding.right
            )
            rendered = Text.assemble(
                (key_text, key_style),
                (description_text, description_style),
            )
        else:
            rendered = Text(self.key_display, style=key_style)
        rendered.stylize_before(self.rich_style)
        return rendered

    def on_mouse_down(self) -> None:
        """Run the advertised binding, or ring the bell when it is disabled."""
        if self.binding_enabled:
            self.app.simulate_key(self.key)
        else:
            self.app.bell()


class OverflowLabel(Label):
    """The ``+3 more`` at the end of a strip that could not show everything."""


class KeyStrip(Footer):
    """Textual's footer, trimmed to the keys the terminal can actually show.

    The stock footer lets the terminal edge cut whatever does not fit, taking
    the last bindings declared, which here are the navigation. This measures
    first, keeps whole entries, and spends its last columns saying how many it
    dropped.
    """

    DEFAULT_CSS: ClassVar[str] = """
    KeyStrip.-compact BindingHint {
        margin-right: 1;
    }
    """

    bindings_ready: reactive[bool] = reactive(False, repaint=False)
    """Whether Textual has calculated the active bindings for this screen."""

    def compose(self) -> ComposeResult:
        if not self.bindings_ready:
            return
        entries = strip_entries(self.screen)
        # `self.size` is zero until the first layout; the strip is full width,
        # so the app's own width is the same number one refresh earlier.
        budget = self.size.width or self.app.size.width
        marker = footer_key_cost("", OVERFLOW_TEMPLATE.format(count=len(entries)))
        shown = keys_that_fit([entry.cost for entry in entries], budget, marker)
        for entry in entries[:shown]:
            yield BindingHint(
                entry.key,
                entry.display,
                entry.description,
                entry.action,
                disabled=not entry.enabled,
                tooltip=entry.tooltip,
            ).data_bind(compact=Footer.compact)
        if hidden := len(entries) - shown:
            yield OverflowLabel(OVERFLOW_TEMPLATE.format(count=hidden))

    def on_resize(self) -> None:
        """A narrower terminal shows fewer keys, so the trim is re-measured.

        Textual recomposes the footer when the *bindings* change, which is all
        the stock widget's output depends on. This one also depends on the width.
        """
        self.call_after_refresh(self.recompose)

    def bindings_changed(self, screen: Screen[object]) -> None:
        """Recompose after Textual publishes a fresh active-binding map."""
        self.bindings_ready = True
        if not screen.app.app_focus:
            return
        if self.is_attached and screen is self.screen:
            self.call_after_refresh(self.recompose)


class AppFooter(Vertical):
    """The status line and the key strip, docked as one unit.

    The status line sits above the keys, so it is the first thing the eye meets
    reading up from the bottom of a terminal.
    """

    DEFAULT_CLASSES: ClassVar[str] = "app-footer"

    def compose(self) -> ComposeResult:
        yield StatusBar()
        # The palette already has its own binding in the app, and Textual's
        # footer would add a second entry for it.
        yield KeyStrip(compact=True, show_command_palette=False)

    def set_status(
        self, message: str, tone: Tone = Tone.NEUTRAL, *, pill: str = ""
    ) -> None:
        self.query_one(StatusBar).set_status(message, tone, pill=pill)
