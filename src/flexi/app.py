"""The application shell: theme, services, screens, jump mode.

The theme is registered in ``__init__`` rather than ``on_mount``, because
setting ``App.theme`` raises if ``register_theme`` has not run and the setup
screen can be pushed before ``on_mount`` finishes.

``/`` is bound with ``priority=True`` so it works from any screen, and stood
down by :meth:`check_action` inside a text field, where a date being typed is
allowed to contain one.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from functools import partial
from pathlib import Path, PurePath
from threading import Event, Lock
from typing import ClassVar

from textual import events, log
from textual import work as textual_work
from textual.app import App as TextualApp
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.command import Provider
from textual.css.query import NoMatches
from textual.reactive import Reactive, reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, TextArea

import flexi
from flexi.components.chrome import NAV_BY_SCREEN, NAV_ITEMS, NavBar
from flexi.components.jump_overlay import JumpOverlay
from flexi.components.jumper import (
    HasFocusTarget,
    HasJumpOverlays,
    HasJumpTargets,
    Jumper,
    Refreshable,
)
from flexi.config import CONFIG
from flexi.messages import BankHolidayRefreshCompleted, Scope
from flexi.models.database.engine import database_scope
from flexi.provider import FlexiCommands
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.help import HelpScreen, collect_bindings
from flexi.screens.insights import InsightsScreen
from flexi.screens.leave import LeaveScreen
from flexi.screens.settings import SettingsScreen
from flexi.screens.setup import SetupScreen
from flexi.services.bank_holidays import (
    BankHolidayFetcher,
    fetch_bank_holiday_index,
)
from flexi.services.registry import build_services, invalidate_services
from flexi.theme import THEME_NAME, flexi_theme
from flexi.versioning import available_update

__all__ = ("UPDATE_NOTICE_SECONDS", "FlexiApp")

UPDATE_NOTICE_SECONDS = 10


class FlexiApp(TextualApp[None]):
    """Flexi."""

    TITLE = "flexi"

    HELP_LABEL = "Anywhere"

    CSS_PATH: ClassVar[list[str | PurePath]] = [
        "theme/flexi.tcss",
        "styles/dashboard.tcss",
        "styles/leave.tcss",
    ]

    COMMANDS: ClassVar[set[type[Provider] | Callable[[], type[Provider]]]] = {
        FlexiCommands
    }

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

    _jumping: Reactive[bool] = reactive(False, init=False, bindings=True)
    """True while the jump overlay is open."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        bank_holiday_fetcher: BankHolidayFetcher = fetch_bank_holiday_index,
    ) -> None:
        super().__init__()
        with ExitStack() as construction:
            self._engine, self._session = construction.enter_context(
                database_scope(db_path)
            )
            self.services = build_services(self._session)
            # Before anything can be pushed: `App.theme = x` raises if the theme has
            # not been registered, and setup is pushed from `on_mount`.
            self.register_theme(flexi_theme())
            self.theme = THEME_NAME
            self.jumper: Jumper | None = None
            self.show_splash = False
            """Set by `flexi init`. Only the first run earns the animation."""
            self.open_settings = False
            """Set by `flexi init` when the answer chosen there was to change them."""
            self._pushed: Screen[None] | None = None
            """The one destination open on top of the dashboard, if any."""
            """The screen `action_go_to` pushed, so `f1` can dismiss it.

            Held rather than found with `isinstance(self.screen, ...)`: `App.screen`
            is typed as `Screen[object]` and narrowing it against a `Screen[None]`
            gives mypy `Never`."""
            self._bank_holiday_fetcher = bank_holiday_fetcher
            self._holiday_refresh_lock = Lock()
            self._shutdown_event = Event()
            self._database_lifetime = construction.pop_all()

    # -- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        if self.services.settings.is_setup_complete():
            # The CLI sweeps when it opens the database and the application did
            # not, so a session left open overnight was still drawn as running
            # since yesterday morning until something wrote to it.
            self.services.clock.sweep()
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
        self.refresh_holidays()

    def _on_setup_done(self, completed: bool | None) -> None:
        if not completed:
            self.exit()
            return
        self.push_screen(DashboardScreen(self.services, id="dashboard"))

    def on_unmount(self) -> None:
        # Textual cannot stop a synchronous request already inside a worker
        # thread. Mark shutdown first so a queued or late completion can never
        # reach the database after its lifetime has closed.
        self._shutdown_event.set()
        self._database_lifetime.close()

    def refresh_holidays(self, *, force: bool = False) -> None:
        """Request a holiday refresh without blocking Textual's message loop.

        Freshness is database state, so it is checked here on the message loop.
        Only the concrete network call is delegated to a worker. This split
        keeps the SQLAlchemy session and the engine's database lease inside the
        application lifetime and on their owning thread.

        ``force`` is the explicit command-palette path. Normal startup respects
        a fresh cache; an explicit refresh always asks GOV.UK. The network lock
        serialises repeated requests, while their completion messages serialize
        validation and replacement naturally on the message loop.
        """
        if self._shutdown_event.is_set():
            return
        if not force and self.services.bank_holidays.is_fresh():
            # Nothing changed, so nothing needs redrawing -- and refetching
            # would put a GOV.UK timeout in front of a current calendar.
            return
        self.fetch_holiday_payload(forced=force)

    @textual_work(thread=True)
    def fetch_holiday_payload(self, *, forced: bool) -> None:
        """Fetch one untrusted calendar payload without touching persistence."""
        with self._holiday_refresh_lock:
            payload = self._bank_holiday_fetcher()

        # ``post_message`` is thread-safe and declines a closed message pump.
        # The event closes the smaller race where unmount begins immediately
        # before this check; the handler repeats it before touching services.
        if not self._shutdown_event.is_set():
            self.post_message(
                BankHolidayRefreshCompleted(payload, forced=forced),
            )

    def on_bank_holiday_refresh_completed(
        self, message: BankHolidayRefreshCompleted
    ) -> None:
        """Persist a worker result while the message-loop database is alive."""
        if self._shutdown_event.is_set():
            return
        fetched = self.services.bank_holidays.cache_payload(message.payload)
        self.finish_holiday_refresh(fetched=fetched, forced=message.forced)

    def finish_holiday_refresh(self, *, fetched: bool, forced: bool) -> None:
        """Apply one holiday worker result on Textual's message loop.

        Public because the worker/message-loop hand-off is a reusable app
        boundary, not an implementation detail hidden behind the palette.
        """
        if fetched:
            self.holidays_refreshed()
        if forced:
            self.notify(
                "Bank holidays refreshed" if fetched else "Could not reach gov.uk",
                severity="information" if fetched else "warning",
                timeout=4,
            )
        elif not fetched:
            self.notify(
                "No bank holiday calendar. Days off will count as working days.",
                severity="warning",
                timeout=UPDATE_NOTICE_SECONDS,
            )

    def holidays_refreshed(self) -> None:
        """The calendar changed under the application. Show the new one.

        Every figure on the dashboard depends on which days are holidays -- a
        bank holiday expects nothing, and without one it is a working day
        nobody worked, worth a day of deficit each. Both routes that rewrite
        the cache used to leave the screen showing the old answer: the worker
        did nothing at all, and the command-palette entry invalidated the
        ledger cache without asking anything to redraw, so the correction
        appeared on the next unrelated keystroke.

        Persistence now happens on this session and this message loop before
        the redraw, so there is no cross-session snapshot to reconcile.
        """
        self.refresh_open_screens()

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
        board = self.dashboard()
        if name == "insights" and board is not None:
            self._open(name, InsightsScreen(board.period))
            return
        if name == "leave" and board is not None:
            self._open(name, LeaveScreen(self.services, board.period.anchor))
            return
        if name == "dashboard":
            # Insights and Leave are pushed screens, so returning to the
            # dashboard means leaving whichever is open. Without this, f1 set
            # the nav label and nothing else, and escape was the only way back.
            self._close_pushed()
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

    def _open(self, name: str, screen: Screen[None]) -> None:
        """Show a pushed destination, closing any other that is already open.

        `f3` then `f2` used to push Leave on top of Insights and overwrite the
        only reference to it, so the stack grew and `f1` — which dismisses what
        it is holding — took Leave off and revealed Insights, while the nav bar
        said Dashboard. One destination is open at a time, so opening one closes
        the last.
        """
        self._close_pushed()
        self._pushed = screen
        self.nav = name
        self.push_screen(screen, callback=partial(self._back, screen))

    def _close_pushed(self) -> None:
        """Dismiss whatever destination is open, if any.

        Cleared before the dismissal rather than after, so the callback can tell
        "this screen was replaced" from "the user left it".
        """
        if self._pushed is None:
            return
        leaving, self._pushed = self._pushed, None
        leaving.dismiss(None)

    def _back(self, screen: Screen[None], _result: object = None) -> None:
        """Leaving a pushed screen returns the nav bar to where the user is.

        Ignored when the screen has already been replaced by another
        destination: the dismissal that swap performed must not drag the nav
        label back to the dashboard behind the screen that replaced it.
        """
        if self._pushed is not screen:
            return
        self._pushed = None
        self.nav = "dashboard"

    def _on_settings_saved(self, saved: bool | None) -> None:
        if not saved:
            return
        self.refresh_open_screens()

    def refresh_open_screens(self, scope: Scope = Scope.ALL) -> None:
        """Something was written behind the screens. Redraw whichever are up.

        Every screen on the stack that can redraw, not the dashboard alone.
        Both callers singled the dashboard out, so saving settings while the
        leave screen was open left it showing a year measured against the
        working pattern that had just been replaced -- and `LeaveScreen`
        carried a `refresh_modules` written "so the app can treat every screen
        alike" that the app never called.
        """
        invalidate_services(self.services)
        for screen in self.screen_stack:
            if isinstance(screen, Refreshable):
                try:
                    screen.refresh_modules(scope)
                except NoMatches:
                    # Still being built. Widgets compose depth by depth, so a
                    # completion message arriving while the dashboard mounts
                    # can land on a module whose own cells are not in the tree
                    # yet.
                    #
                    # Caught here rather than guarded at each widget: there is
                    # no flag that means "my whole subtree is composed"
                    # (`is_mounted` is true well before it), and a screen that
                    # is still mounting draws itself from `on_mount` a moment
                    # later, with the data this call had already committed.
                    #
                    # This cannot hide a mistyped selector. Every module calls
                    # `rebuild` directly from its own `on_mount`, which is not
                    # this path, so a bad id still fails loudly on the first
                    # draw.
                    continue

    def dashboard(self) -> DashboardScreen | None:
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
        screen = self.dashboard()
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

        # `isinstance` against a runtime-checkable Protocol rather than three
        # `getattr` lookups by string. A renamed hook was valid Python, clean
        # under `--strict`, and a jump mode that silently offered nothing --
        # which is the failure `context.py` was written to abolish, one level
        # up.
        screen = self.screen
        self.jumper = Jumper(
            screen.jump_targets() if isinstance(screen, HasJumpTargets) else {},
            screen=screen,
            extra=(
                screen.jump_overlays if isinstance(screen, HasJumpOverlays) else None
            ),
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

    def _jump_to_id(self, target: str) -> None:
        """Focus the target, or click it if it cannot take focus.

        A row key lands here too: the records table owns the cursor rather than
        the focus, so a `d-` key moves the cursor and focuses the table.
        """
        from flexi.components.expandable import ExpandableTable, RowKind

        if target.startswith(RowKind.DAY):
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
        focus_on = (
            widget.focus_target() if isinstance(widget, HasFocusTarget) else widget
        )
        if focus_on.focusable:
            self.set_focus(focus_on)
        else:
            # Not focusable: a button, say. Synthesise the click the pointer
            # would have made, so a jump can press things too.
            widget.post_message(
                events.Click(widget, 0, 0, 0, 0, 0, False, False, False)
            )
