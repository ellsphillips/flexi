"""The application shell: theme, services, screens, jump mode.

The theme is registered in ``__init__`` rather than ``on_mount``, because
setting ``App.theme`` raises if ``register_theme`` has not run and the setup
screen can be pushed before ``on_mount`` finishes.

``/`` is bound with ``priority=True`` so it works from any screen, and stood
down by :meth:`check_action` inside a text field, where a date being typed is
allowed to contain one.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any, ClassVar, cast

from textual import events, log
from textual import work as textual_work
from textual.app import App as TextualApp
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.css.query import NoMatches
from textual.reactive import Reactive, reactive
from textual.widget import Widget
from textual.widgets import Input, TextArea

import flexi
from flexi.components.chrome import NAV_BY_SCREEN, NAV_ITEMS, NavBar
from flexi.components.jump_overlay import JumpOverlay
from flexi.components.jumper import Jumper
from flexi.config import CONFIG
from flexi.models.database.app import create_db_engine, get_session
from flexi.provider import FlexiCommands
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.help import HelpScreen, collect_bindings
from flexi.screens.insights import InsightsScreen
from flexi.screens.leave import LeaveScreen
from flexi.screens.settings import SettingsScreen
from flexi.screens.setup import SetupScreen
from flexi.services.registry import Services
from flexi.services.startup import run_startup_cleanup
from flexi.theme import THEME_NAME, flexi_theme
from flexi.versioning import available_update

UPDATE_NOTICE_SECONDS = 10


class FlexiApp(TextualApp[None]):
    """Flexi."""

    TITLE = "flexi"

    CSS_PATH: ClassVar[list[str | PurePath]] = [
        "theme/flexi.tcss",
        "styles/dashboard.tcss",
        "styles/leave.tcss",
    ]

    COMMANDS: ClassVar[set[Any]] = {FlexiCommands}

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            CONFIG.hotkeys.clock_toggle,
            "clock_toggle",
            "Clock",
            show=True,
            priority=True,
        ),
        Binding(CONFIG.hotkeys.toggle_jump_mode, "toggle_jump_mode", "Jump", show=True),
        Binding(CONFIG.hotkeys.help, "help", "Help", show=True),
        *[
            Binding(item.key, f"go_to('{item.screen}')", item.label, show=True)
            for item in NAV_ITEMS
        ],
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    nav: Reactive[str] = reactive("dashboard", init=False)
    """Which destination is current, read by the nav bar when it composes."""

    context_label: Reactive[str] = reactive("", init=False)
    """The right-hand slot of the header: today's date and the shown period."""

    _jumping: Reactive[bool] = reactive(False, init=False, bindings=True)
    """True while the jump overlay is open."""

    def __init__(self, *, db_path: Any = None) -> None:
        super().__init__()
        self._engine = create_db_engine(db_path) if db_path else create_db_engine()
        self._session = get_session(self._engine)
        self.services = Services.build(self._session)
        # Before anything can be pushed: `App.theme = x` raises if the theme has
        # not been registered, and setup is pushed from `on_mount`.
        self.register_theme(flexi_theme())
        self.theme = THEME_NAME
        self.jumper: Jumper | None = None
        self.show_splash = False
        """Set by `flexi init`. Only the first run earns the animation."""
        self.open_settings = False
        """Set by `flexi init` when the answer chosen there was to change them."""
        self._pushed: InsightsScreen | LeaveScreen | None = None
        """The screen `action_go_to` pushed, so `f1` can dismiss it.

        Held rather than found with `isinstance(self.screen, ...)`: `App.screen`
        is typed as `Screen[object]` and narrowing it against a `Screen[None]`
        gives mypy `Never`."""

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        if self.services.settings.is_setup_complete():
            # The CLI sweeps when it opens the database and the application did
            # not, so a session left open overnight was still drawn as running
            # since yesterday morning until something wrote to it.
            run_startup_cleanup(
                self._session,
                self.services.clock,
                self.services.settings.get_auto_close_time(),
            )
            self.push_screen(DashboardScreen(self.services, id="dashboard"))
            if self.open_settings:
                self.push_screen(
                    SettingsScreen(self.services), callback=self._on_settings_saved
                )
        else:
            # The wordmark is part of the setup screen rather than a screen of
            # its own pushed over it. `Screen.dismiss` pops the top of the stack
            # rather than the screen it is called on, so an animation on its own
            # screen dismissed the form underneath instead of itself. One screen
            # means there is nothing left to pop.
            from flexi.components.wordmark import wanted

            plays = self.show_splash and wanted(animation_level=self.animation_level)
            self.push_screen(
                SetupScreen(self.services, animate=plays),
                callback=self._on_setup_done,
            )
        self._check_for_updates()
        self._refresh_holidays()

    def _on_setup_done(self, completed: bool | None) -> None:
        if not completed:
            self.exit()
            return
        # Rebuild: the division and the working pattern are chosen during setup,
        # and the services were wired before either existed.
        self.services = Services.build(self._session)
        self.push_screen(DashboardScreen(self.services, id="dashboard"))

    def on_unmount(self) -> None:
        self._session.close()
        self._engine.dispose()

    @textual_work(thread=True)
    def _refresh_holidays(self) -> None:
        """Keep the bank holiday calendar current, off the message loop.

        A worker rather than a blocking call at mount: this is a network round
        trip, and the dashboard should not wait on GOV.UK to draw. Nothing in
        the application refreshed it at all before -- the only route was a
        command-palette entry somebody had to know about.
        """
        if self.services.bank_holidays.ensure_cache():
            return
        self.notify(
            "No bank holiday calendar. Days off will count as working days.",
            severity="warning",
            timeout=UPDATE_NOTICE_SECONDS,
        )

    @textual_work(thread=True)
    def _check_for_updates(self) -> None:
        """Ask PyPI whether there is a newer Flexi, and say nothing if not."""
        latest = available_update()
        if latest is None:
            return
        self.notify(
            f"Update available: {flexi.__version__} → {latest}\n"
            f"Run: uv tool upgrade flexi",
            severity="information",
            timeout=UPDATE_NOTICE_SECONDS,
        )

    # -- navigation --------------------------------------------------------

    def action_go_to(self, name: str) -> None:
        """Move to a destination from the one navigation table."""
        if name == self.nav:
            return
        if name == "settings":
            self.push_screen(
                SettingsScreen(self.services), callback=self._on_settings_saved
            )
            return
        board = self._dashboard()
        if name == "insights" and board is not None:
            self.nav = name
            self._pushed = InsightsScreen(self.services, board.period)
            self.push_screen(self._pushed, callback=self._back)
            return
        if name == "leave" and board is not None:
            self.nav = name
            self._pushed = LeaveScreen(self.services, board.period.anchor)
            self.push_screen(self._pushed, callback=self._back)
            return
        if name == "dashboard":
            # Insights is a pushed screen, so returning to the dashboard means
            # leaving it. Without this, f1 set the nav label and nothing else,
            # and escape was the only way back.
            if self._pushed is not None:
                self._pushed.dismiss(None)
                self._pushed = None
            self.nav = name
            return
        item = NAV_BY_SCREEN.get(name)
        self.notify(
            f"{item.label if item else name} is not built yet.",
            severity="information",
            timeout=3,
        )

    def on_nav_bar_selected(self, event: NavBar.Selected) -> None:
        """A tab was clicked. The keys and the pointer arrive at one place.

        `NavItemLabel` is a widget with a hover state rather than a line of
        markup, so that a pointer works — which it does not until somebody
        handles the message it posts.
        """
        event.stop()
        self.action_go_to(event.item.screen)

    def _back(self, _result: object = None) -> None:
        """Leaving a pushed screen returns the nav bar to where the user is."""
        self._pushed = None
        self.nav = "dashboard"

    def _on_settings_saved(self, saved: bool | None) -> None:
        if not saved:
            return
        self.services = Services.build(self._session)
        self.services.invalidate()
        screen = self._dashboard()
        if screen is not None:
            from flexi.messages import Scope

            screen.refresh_modules(Scope.ALL)

    def _dashboard(self) -> DashboardScreen | None:
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                return screen
        return None

    # -- clocking ----------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Stand `/` down while somebody is typing.

        The binding is `priority=True`, so it runs before the focused widget -- without
        this it would eat the slash out of a date being typed into "go to date".
        """
        del parameters
        if action != "clock_toggle":
            return True
        return not isinstance(self.focused, Input | TextArea)

    def action_clock_toggle(self) -> None:
        """One key, from anywhere. The dashboard owns the confirmation."""
        screen = self._dashboard()
        if screen is None:
            return
        screen.toggle_clock()

    # -- help --------------------------------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpScreen(collect_bindings(self.screen)))

    # -- jump mode ---------------------------------------------------------

    def action_toggle_jump_mode(self) -> None:
        self._jumping = not self._jumping

    def watch__jumping(self) -> None:
        focused_before = self.focused
        if focused_before is not None:
            self.set_focus(None, scroll_visible=False)

        self.jumper = Jumper(
            self._jump_targets(),
            screen=self.screen,
            extra=getattr(self.screen, "jump_overlays", None),
        )

        def handle(target: str | Widget | None) -> None:
            if isinstance(target, str):
                self._jump_to_id(target)
            elif isinstance(target, Widget):
                self.set_focus(target)
            elif focused_before is not None:
                # Escape. Put focus back exactly where it was — a mode you can
                # leave without consequence is one people will keep using.
                self.set_focus(focused_before, scroll_visible=False)

        self.clear_notifications()
        self.push_screen(JumpOverlay(self.jumper), callback=handle)

    def _jump_targets(self) -> dict[str, str]:
        getter = getattr(self.screen, "jump_targets", None)
        return dict(getter()) if callable(getter) else {}

    def _jump_to_id(self, target: str) -> None:
        """Focus the target, or click it if it cannot take focus.

        A row key lands here too: the records table owns the cursor rather than
        the focus, so a `d-` key moves the cursor and focuses the table.
        """
        from flexi.components.expandable import DAY, ExpandableTable

        if target.startswith(DAY):
            for table in self.screen.query(ExpandableTable):
                table.focus_key(target)
                self.set_focus(table)
                return
            return

        try:
            widget = self.screen.query_one(f"#{target}")
        except NoMatches:
            log.warning(f"jump target #{target} is not on {self.screen!r}")
            return
        focus_on: Widget = widget
        chooser = getattr(widget, "focus_target", None)
        if callable(chooser):
            focus_on = cast("Widget", chooser())
        if focus_on.focusable:
            self.set_focus(focus_on)
        else:
            # Not focusable: a button, say. Synthesise the click the pointer
            # would have made, so a jump can press things too.
            widget.post_message(
                events.Click(widget, 0, 0, 0, 0, 0, False, False, False)
            )


App = FlexiApp
"""The v1 name, kept so ``flexi.__main__`` and older tests keep importing."""
