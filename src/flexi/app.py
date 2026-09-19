"""The application shell: theme, services, screens, jump mode.

``register_theme`` runs in ``__init__``, not ``on_mount``: setting ``App.theme``
raises before the theme is registered, and the setup screen can be pushed before
``on_mount`` finishes.

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
from flexi.components.chrome import NAV_ITEMS, AppFooter, AppHeader, NavBar, stamped
from flexi.components.jump_overlay import JumpOverlay
from flexi.components.jumper import (
    HasFocusTarget,
    HasJumpOverlays,
    HasJumpTargets,
    Jumper,
    Refreshable,
)
from flexi.config import CONFIG, CONFIG_PROBLEM
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
from flexi.versioning import UPGRADE_HINT, available_update

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
    """Which destination is current; the nav bar reads it when it composes."""

    _jumping: Reactive[bool] = reactive(False, init=False, bindings=True)

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
            # `App.theme = x` raises unless `register_theme` has already run,
            # and `on_mount` pushes a screen.
            self.register_theme(flexi_theme())
            self.theme = THEME_NAME
            self.jumper: Jumper | None = None
            self.show_splash = False
            """Set by `flexi init` on a first run, to play the splash."""
            self.open_settings = False
            """Set by `flexi init` when the user chose to change settings."""
            self._pushed: Screen[None] | None = None
            """The one destination open on top of the dashboard, if any.

            Held as an attribute, not found with `isinstance(self.screen, ...)`:
            `App.screen` is typed as `Screen[object]`, and narrowing it against
            a `Screen[None]` gives mypy `Never`."""
            self._settings: SettingsScreen | None = None
            """The settings form, if it is open.

            Kept separate from `_pushed`: `SettingsScreen` is a `Screen[bool]`,
            `Screen`'s parameter is invariant, and assigning one to a
            `Screen[None]` is a mypy error."""
            self._bank_holiday_fetcher = bank_holiday_fetcher
            self._holiday_refresh_lock = Lock()
            self._shutdown_event = Event()
            self.latest_release = ""
            """The published version superseding this one, once one is known."""
            self._database_lifetime = construction.pop_all()

    # lifecycle ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        if self.services.settings.is_setup_complete():
            # The CLI sweeps when it opens the database, and so does this: a
            # session left open overnight otherwise draws as still running.
            self.services.clock.sweep()
            self.push_screen(DashboardScreen(self.services, id="dashboard"))
            if self.open_settings:
                # Held like any other form, so `f4` cannot push a second over
                # it and `f1` can close it.
                self._settings = SettingsScreen(self.services)
                self.push_screen(self._settings, callback=self._on_settings_saved)
        else:
            # The wordmark is part of the setup screen, not a screen pushed over
            # it: `Screen.dismiss` pops the top of the stack, not the screen it
            # is called on, so a splash on its own screen would dismiss the form
            # underneath.
            from flexi.components.wordmark import wanted

            plays = self.show_splash and wanted(animation_level=self.animation_level)
            self.push_screen(
                SetupScreen(self.services, animate=plays),
                callback=self._on_setup_done,
            )
        self._check_for_updates()
        self.refresh_holidays()
        if CONFIG_PROBLEM:
            # `BINDINGS` reads `CONFIG` at class scope, with no screen to say
            # this on; here is the first moment there is one.
            self.notify(
                CONFIG_PROBLEM, severity="warning", timeout=UPDATE_NOTICE_SECONDS
            )

    def _on_setup_done(self, completed: bool | None) -> None:  # noqa: FBT001 - Textual passes a dismissal result positionally
        if not completed:
            self.exit()
            return
        self.push_screen(DashboardScreen(self.services, id="dashboard"))
        # Freshness is per division and the division was only just answered, so
        # without this a Scottish or Northern Irish first run reaches the
        # dashboard with no calendar of its own.
        self.refresh_holidays()

    def on_unmount(self) -> None:
        # Textual cannot stop a synchronous request already inside a worker
        # thread. The flag is set first, so a late completion cannot reach the
        # database after its lifetime has closed.
        self._shutdown_event.set()
        self._database_lifetime.close()

    def refresh_holidays(self, *, force: bool = False) -> None:
        """Request a holiday refresh without blocking Textual's message loop.

        Freshness is database state, so it is checked here, on the message loop;
        only the network call goes to a worker, keeping the session and the
        engine's database lease on their owning thread. ``force`` is the
        command-palette path, and always asks GOV.UK.
        """
        if self._shutdown_event.is_set():
            return
        if not force and self.services.bank_holidays.is_fresh():
            # A fresh cache needs no redraw, and refetching would put a GOV.UK
            # timeout in front of a current calendar.
            return
        self.fetch_holiday_payload(forced=force)

    @textual_work(thread=True, exit_on_error=False)
    def fetch_holiday_payload(self, *, forced: bool) -> None:
        """Fetch one untrusted calendar payload without touching persistence."""
        with self._holiday_refresh_lock:
            try:
                payload = self._bank_holiday_fetcher()
            except Exception:  # noqa: BLE001 - an injected fetcher may raise anything
                # A failed fetch is a completion with nothing in it. An
                # exception escaping the worker would take the application down
                # and leave the message below unposted.
                payload = None

        # ``post_message`` is thread-safe and declines a closed message pump.
        # The event covers the race where unmount begins just before this check;
        # the handler repeats it before touching services.
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
        """Apply one holiday worker result on Textual's message loop."""
        if fetched:
            self.holidays_refreshed()
        if forced:
            self.notify(
                "Bank holidays refreshed" if fetched else "Could not reach gov.uk",
                severity="information" if fetched else "warning",
                timeout=4,
            )
        elif not fetched and not self.services.bank_holidays.is_available():
            # Only when nothing is cached: a stale calendar is still the one
            # every figure on screen is derived from.
            self.notify(
                "No bank holiday calendar. Days off will count as working days.",
                severity="warning",
                timeout=UPDATE_NOTICE_SECONDS,
            )

    def holidays_refreshed(self) -> None:
        """Redraw the open screens: every figure depends on which days are off."""
        self.refresh_open_screens()

    @textual_work(thread=True, exit_on_error=False)
    def _check_for_updates(self) -> None:
        """Ask PyPI whether there is a newer Flexi, and say nothing if not."""
        latest = available_update()
        if latest is None:
            return
        self.call_from_thread(self.update_offered, latest)
        self.notify(
            f"Update available: {stamped(flexi.__version__)} → {stamped(latest)}\n"
            f"{UPGRADE_HINT}",
            severity="information",
            timeout=UPDATE_NOTICE_SECONDS,
        )

    def update_offered(self, latest: str) -> None:
        """Remember that a newer version exists, and say so on every header.

        Called on the message loop: the check runs on a thread, and writing a
        reactive from one refreshes widgets off the loop.
        """
        self.latest_release = latest
        self.dress_headers()

    def dress_headers(self) -> None:
        """Tell every header on the stack what the app knows about the release.

        The value travels down: a header may be mounted without an application
        to reach up to, and a screen opened after the check has not been told.
        """
        if not self.latest_release:
            return
        for screen in self.screen_stack:
            for header in screen.query(AppHeader):
                header.offer_update(self.latest_release)

    # navigation --------------------------------------------------------------

    def action_go_to(self, name: str) -> None:
        """Move to a destination from the one navigation table."""
        board = self.dashboard()
        if board is None:
            # No dashboard means setup is still open, and every destination is
            # drawn from the period the dashboard holds. Settings is refused
            # with the rest: saving it marks the install configured, which would
            # end setup without an entitlement having been answered.
            self.notify("Finish setup first.", severity="information", timeout=3)
            return
        if name == self.nav:
            # Settings has no nav item, so `self.nav` still names the
            # destination underneath an open form. Choosing the destination you
            # are on closes the form over it and nothing else.
            self._close_settings()
            return
        if name == "settings":
            # `self.nav` is only ever set to a destination with a nav item, so
            # the guard above cannot cover settings. A second form would hold
            # the field values read at its construction and write them back over
            # the first form's save.
            if self._settings is not None:
                return
            # `_close_pushed()` is not called here: settings sits *on top of*
            # whatever destination is open, and the callback redraws that screen
            # after a save.
            self._settings = SettingsScreen(self.services)
            self.push_screen(self._settings, callback=self._on_settings_saved)
            return
        if name == "insights":
            self._open(name, InsightsScreen(board.period))
            return
        if name == "leave":
            self._open(name, LeaveScreen(self.services, board.period.anchor))
            return
        # Insights and Leave are pushed screens, so returning to the dashboard
        # means dismissing whichever of them is open.
        self._close_pushed()
        self.nav = name

    def on_nav_bar_selected(self, event: NavBar.Selected) -> None:
        """Route a clicked tab through the same path as the key bindings."""
        event.stop()
        self.action_go_to(event.item.screen)

    def _open(self, name: str, screen: Screen[None]) -> None:
        """Show a pushed destination, closing any other that is already open.

        One destination is open at a time: pushing a second over the first would
        overwrite the only reference to it and leave it on the stack.
        """
        self._close_pushed()
        self._pushed = screen
        self.nav = name
        self.push_screen(screen, callback=partial(self._back, screen))
        # After the refresh: the pushed screen composes its header on the way
        # in, and there is no header to tell until it has.
        self.call_after_refresh(self.dress_headers)

    def _close_pushed(self) -> None:
        """Dismiss whatever destination is open, if any.

        `_pushed` is cleared before the dismissal, so the callback can tell "this
        screen was replaced" from "the user left it". Settings is closed first,
        because it sits on top and `Screen.dismiss` pops whatever is on top of
        the stack, not the screen it was called on.
        """
        self._close_settings()
        if self._pushed is None:
            return
        leaving, self._pushed = self._pushed, None
        leaving.dismiss(None)

    def _close_settings(self) -> None:
        """Dismiss the settings form if one is open, clearing `_settings` first.

        A callback arriving during the dismissal then cannot find the form it is
        closing.
        """
        if self._settings is None:
            return
        form, self._settings = self._settings, None
        form.dismiss(False)

    def _back(self, screen: Screen[None], _result: object = None) -> None:
        """Return the nav bar to the dashboard when a pushed screen is dismissed.

        Ignored once the screen has been replaced: the dismissal that swap
        performs must not drag the nav label back behind its replacement.
        """
        if self._pushed is not screen:
            return
        self._pushed = None
        self.nav = "dashboard"

    def _on_settings_saved(self, saved: bool | None) -> None:  # noqa: FBT001 - Textual passes a dismissal result positionally
        """Forget the closed form, and redraw if it was saved."""
        self._settings = None
        if not saved:
            return
        self.refresh_open_screens()
        # Freshness is per division, so this is a no-op unless the division has
        # just changed to one with no calendar; leave bookings are refused for
        # the rest of the session without one.
        self.refresh_holidays()

    def refresh_open_screens(self, scope: Scope = Scope.ALL) -> None:
        """Redraw every screen on the stack that can redraw.

        Not the dashboard alone: a screen behind an open form is showing figures
        derived from whatever was just written.
        """
        invalidate_services(self.services)
        for screen in self.screen_stack:
            if isinstance(screen, Refreshable):
                try:
                    screen.refresh_modules(scope)
                except NoMatches:
                    # Still being built: widgets compose depth by depth, so a
                    # message arriving while the dashboard mounts can land on a
                    # module whose own cells are not in the tree yet. There is
                    # no flag meaning "my subtree is composed" (`is_mounted` is
                    # true well before it), and a mounting screen redraws from
                    # `on_mount` with the data this call has committed.
                    continue

    def dashboard(self) -> DashboardScreen | None:
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                return screen
        return None

    def showing_dashboard(self) -> bool:
        """Whether the dashboard is the destination in front of the user.

        Answered from what is open, not from `self.screen`: the command palette
        is itself a pushed screen, so `self.screen` is the palette while a
        command is being chosen.
        """
        return self._pushed is None and self._settings is None

    # clocking ----------------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Stand `/` down while an Input or TextArea has focus.

        The binding is `priority=True`, so it runs before the focused widget and
        would otherwise eat the slash out of a date being typed.
        """
        del parameters
        if action != "clock_toggle":
            return True
        return not isinstance(self.focused, Input | TextArea)

    def action_clock_toggle(self) -> None:
        """Toggle the clock from anywhere, with the receipt on the visible footer.

        The dashboard does the clocking and reports to its own footer, which
        sits underneath Leave and Insights.
        """
        board = self.dashboard()
        if board is None:
            return
        message, tone = board.toggle_clock()
        if self.screen is board:
            return
        for footer in self.screen.query(AppFooter):
            footer.set_status(message, tone)
        self.refresh_open_screens(Scope.CLOCK)

    # help --------------------------------------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpScreen(collect_bindings(self.screen)))

    # jump mode ---------------------------------------------------------------

    def action_toggle_jump_mode(self) -> None:
        self._jumping = not self._jumping

    def watch__jumping(self) -> None:
        focused_before = self.focused
        if focused_before is not None:
            self.set_focus(None, scroll_visible=False)

        # `isinstance` against a runtime-checkable Protocol, not `getattr` by
        # name: a renamed hook stays valid Python and leaves jump mode silently
        # offering nothing.
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
                # Escape: restore the previous focus.
                self.set_focus(focused_before, scroll_visible=False)

        self.clear_notifications()
        self.push_screen(JumpOverlay(self.jumper), callback=handle)

    def _jump_to_id(self, target: str) -> None:
        """Focus the target, or click it if it cannot take focus.

        A row key lands here too: the records table owns the cursor, so a `d-`
        key moves the cursor and focuses the table.
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
            # Not focusable (a button, say): synthesise the click a pointer
            # would have made, so a jump can press things too.
            widget.post_message(
                events.Click(widget, 0, 0, 0, 0, 0, False, False, False)
            )
