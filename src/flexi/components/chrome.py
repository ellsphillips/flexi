"""The frame every screen sits in: header, navigation, status line, key strip.

:data:`NAV_ITEMS` is the one table naming which screens exist, which key reaches
them and what they are called. Bindings, the nav bar and the command palette are
all built from it, so adding a screen is a line here rather than four edits in
three files. It lives in this module rather than in ``app.py`` because the
widgets need it and the app imports the widgets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.widgets._footer import FooterKey, FooterLabel

from flexi.components.common import Pill, Tone

OVERFLOW_TEMPLATE: Final = "+{count} more"
"""Written as a template so the strip can price the marker before it knows the
number: the worst case is every entry hidden, and reserving for that keeps the
reservation from being one column short of the thing it reserved for."""


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

NAV_BY_SCREEN: Final[dict[str, NavItem]] = {item.screen: item for item in NAV_ITEMS}


class Wordmark(Horizontal):
    """`flexi` plus a teal full stop. The only fixed point in the interface."""

    def compose(self) -> ComposeResult:
        yield Static("flexi", classes="wordmark-name")
        yield Static("·", classes="wordmark-dot")


class NavItemLabel(Static):
    """One clickable destination in the nav bar.

    A widget rather than a line of markup so it can hover, and so the click lands
    on the item rather than on the bar and has to be resolved by column.
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
        # Read at compose rather than on mount. A widget's `on_mount` runs before
        # its own children are usable, so anything that reaches for them there
        # quietly does nothing; deciding here means the first frame is already
        # correct and `watch_active` only has to handle changes.
        self.set_reactive(NavBar.active, str(getattr(self.app, "nav", self.active)))
        for item in NAV_ITEMS:
            yield NavItemLabel(item, active=item.screen == self.active)

    def watch_active(self) -> None:
        for label in self.query(NavItemLabel):
            label.set_class(label.item.screen == self.active, "-active")


class AppHeader(Horizontal):
    """Wordmark, navigation, and the date and period in play.

    Reads its context on mount and is pushed to afterwards -- both, because
    Textual's ``ScreenResume`` does not bubble to the app, so a header on a newly
    raised screen has to ask. The app is reached with ``getattr`` to keep this
    module out of an import cycle.
    """

    context: reactive[str] = reactive("", init=False)

    def compose(self) -> ComposeResult:
        self.set_reactive(
            AppHeader.context, str(getattr(self.app, "context_label", ""))
        )
        yield Wordmark()
        yield NavBar()
        yield Static(self.context, classes="header-context", id="header-context")

    def watch_context(self, context: str) -> None:
        if self.is_mounted:
            self.query_one("#header-context", Static).update(context)

    def set_active(self, screen: str) -> None:
        for bar in self.query(NavBar):
            bar.active = screen


class StatusBar(Horizontal):
    """A transient line: what just happened, and one pill of state.

    Distinct from the footer's key hints, which say what you *can* do. This says
    what Flexi *did* — "Clocked in at 09:12", "Annual leave booked for Wed 10" —
    and is where every service result surfaces.
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
        self.query_one("#status-pill", Pill).set_state(pill, tone)


def footer_key_cost(key_display: str, description: str) -> int:
    """Columns one compact footer entry occupies, including its right margin.

    Textual's compact ``FooterKey`` drops the key's padding and puts a single
    space before the description, and the strip gives every entry one column of
    right margin. So an entry is its two strings plus two columns — a formula the
    tests check against measured regions rather than trust.
    """
    return len(key_display) + len(description) + 2


def keys_that_fit(costs: Sequence[int], budget: int, marker: int) -> int:
    """How many entries fit in ``budget``, leaving room to say what did not.

    The last entry on the strip does not need its right margin, hence the -1 in
    both branches. When everything fits there is nothing to announce and the
    marker costs nothing; when it does not, room for the marker is reserved
    before the first entry is admitted, because a strip that overflowed *and* hid
    its overflow notice is the failure this function exists to prevent.
    """
    if sum(costs) - 1 <= budget:
        return len(costs)
    used = 0
    for count, cost in enumerate(costs):
        if used + cost + marker - 1 > budget:
            return count
        used += cost
    # Unreachable for any non-negative marker: getting here needs
    # `sum(costs) + marker - 1 <= budget` while the guard above already
    # established `sum(costs) - 1 > budget`, which together require
    # `marker < 0`. Kept because the loop has to end in a return.
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

    One per *action* rather than one per binding, which is what Textual's own
    footer does and what stops the dashboard advertising ``/ Clock`` and
    ``space Clock`` as two separate things you could press.
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


class OverflowLabel(FooterLabel):
    """The ``+3 more`` at the end of a strip that could not show everything."""


class KeyStrip(Footer):
    """Textual's footer, trimmed to the keys the terminal can actually show.

    The stock footer lets the terminal edge cut whatever does not fit, and what it
    cuts is the last bindings declared -- here, the navigation. This measures
    first, keeps whole entries, and spends its last columns saying how many it
    dropped.
    """

    def compose(self) -> ComposeResult:
        if not self._bindings_ready:
            return
        entries = strip_entries(self.screen)
        # `self.size` is zero until the first layout; the strip is full width, so
        # the app's own width is the same number one refresh earlier.
        budget = self.size.width or self.app.size.width
        marker = footer_key_cost("", OVERFLOW_TEMPLATE.format(count=len(entries)))
        shown = keys_that_fit([entry.cost for entry in entries], budget, marker)
        for entry in entries[:shown]:
            yield FooterKey(
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

        Textual recomposes the footer when the *bindings* change, which is the
        only thing the stock widget's output depends on. Ours also depends on the
        width.
        """
        self.call_after_refresh(self.recompose)


class AppFooter(Vertical):
    """The status line and the key strip, docked as one unit.

    The status line sits above the keys because a message about what just
    happened is more urgent than a list of what could, and the eye reads upwards
    from the bottom of a terminal.
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
