"""The frame around the modules: the nav bar, the status line, and jump mode.

Driven through bare harness applications, because each of these widgets is
ignorant of the application it frames.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any, ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.geometry import Offset
from textual.pilot import Pilot
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Static

from flexi.components import chrome
from flexi.components.chrome import (
    AppHeader,
    BindingHint,
    KeyStrip,
    NavItemLabel,
    StatusBar,
    footer_key_cost,
)
from flexi.components.common import Pill, Tone
from flexi.components.jump_overlay import JumpOverlay
from flexi.components.jumper import Jumper, JumpInfo
from tests.conftest import SETTLE_PASSES, settled

WIDE = (80, 24)


class Framed(App[None]):
    """A header on its own, with no Flexi behind it."""

    def compose(self) -> ComposeResult:
        yield AppHeader()


class Named(Static):
    """A widget carrying its own jump key, in place of being registered."""

    jump_key = "p"


class Jumpy(App[None]):
    """Two side-by-side targets: one registered by id, one naming itself."""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("one", id="one")
            yield Named("two", id="two")


class Bound(App[None]):
    """A single advertised action, for the footer's public binding boundary."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("x", "mark", "Mark", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.marked = False

    def compose(self) -> ComposeResult:
        yield KeyStrip(compact=True, show_command_palette=False)

    def action_mark(self) -> None:
        self.marked = True


def active_labels(app: App[None]) -> set[str]:
    return {
        label.item.screen
        for label in app.query(NavItemLabel)
        if label.has_class("-active")
    }


def showing[S: Screen[Any]](app: App[None], kind: type[S]) -> S:
    """The current screen, asserted to be ``kind``.

    ``App.screen`` is typed ``Screen[object]``, so narrowing it in place against
    a differently parametrised subclass leaves mypy holding ``Never``.
    """
    screen = app.screen
    assert isinstance(screen, kind), (
        f"expected {kind.__name__}, showing {type(screen).__name__}"
    )
    return screen


async def laid_out(pilot: Pilot[Any], widget: Widget) -> None:
    """Wait until the compositor has given ``widget`` a width.

    `settled` waits for the callbacks a layout schedules, not for the layout
    itself, and an unplaced widget reports `region.width == 0`.
    """
    for _ in range(SETTLE_PASSES):
        if widget.region.width:
            return
        await pilot.pause()
    msg = f"{type(widget).__name__} was never laid out"
    raise AssertionError(msg)


# ---- the nav bar ----


async def test_nav_highlight_follows_the_screen() -> None:
    """The bar is composed once and lives on every screen, so the highlight moves."""
    app = Framed()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert active_labels(app) == {"dashboard"}

        app.query_one(AppHeader).set_active("leave")
        await pilot.pause()

        assert active_labels(app) == {"leave"}


# ---- the key strip ----


def test_chrome_uses_only_public_textual_footer_contracts() -> None:
    """A supported Textual minor must not move an implementation out from under us."""
    tree = ast.parse(inspect.getsource(chrome))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not {
        module
        for module in imported
        if module.startswith("textual.")
        and any(part.startswith("_") for part in module.split("."))
    }
    assert "_bindings_ready" not in {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


async def test_binding_hint_measures_and_runs() -> None:
    """The local widget keeps the rendering and click contract it replaces."""
    app = Bound()
    async with app.run_test(size=(40, 10)) as pilot:
        # Settled, not pumped a fixed number of times: the strip composes its
        # hints from a callback the binding map schedules.
        await settled(pilot)
        hint = app.query_one(BindingHint)
        await laid_out(pilot, hint)

        assert str(hint.render()) == "x Mark"
        assert hint.region.width + hint.styles.margin.right == footer_key_cost(
            "x", "Mark"
        )

        await pilot.click(hint)
        await pilot.pause()
        assert app.marked


# ---- writing to chrome that is not there yet ----


def test_unmounted_header_accepts_context() -> None:
    """An unmounted header has no children for its watcher to write into."""
    header = AppHeader()
    header.context = "Thu 11 Jun · This week"
    assert header.context == "Thu 11 Jun · This week"


async def test_status_before_mount_is_dropped() -> None:
    """Dropped, not held over: a stale result must not overwrite later work."""
    bar = StatusBar()
    bar.set_status("Clocked in at 09:12", Tone.OK, pill="on the clock")

    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await app.mount(bar)
        await pilot.pause()

        assert str(bar.query_one("#status-message", Static).render()) == ""
        assert str(bar.query_one("#status-pill", Pill).render()) == ""


async def test_status_pill_labels_the_tone() -> None:
    """The tone reaches the pill and nowhere else, so colour is never alone."""
    bar = StatusBar()
    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await app.mount(bar)
        await pilot.pause()
        pill = bar.query_one("#status-pill", Pill)

        bar.set_status("There is recorded work in that part of the day", Tone.ERR)
        await pilot.pause()
        assert str(pill.render()) == "refused"
        assert pill.display is True
        assert pill.has_class("pill--err")

        bar.set_status("Clocked in at 09:12", Tone.OK, pill="on the clock")
        await pilot.pause()
        assert str(pill.render()) == "on the clock", "a caller's own word wins"

        bar.set_status("Showing June")
        await pilot.pause()
        assert pill.display is False, "a toneless status has no state to tag"


# ---- jump targets ----


async def test_widget_names_its_own_jump_key() -> None:
    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        overlays = Jumper({"one": "o"}, app.screen).get_overlays()

        assert {info.key for info in overlays.values()} == {"o", "p"}
        assert overlays[app.screen.get_offset(app.query_one(Named))] == JumpInfo(
            "p", app.query_one(Named)
        )


async def test_screen_adds_non_widget_targets() -> None:
    """A table row has no id and no rectangle, so the screen supplies its offset."""
    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        row = Offset(40, 10)
        jumper = Jumper(
            {"one": "o"}, app.screen, lambda: {row: JumpInfo("1", "d-2026-06-11")}
        )

        overlays = jumper.get_overlays()
        assert overlays[row] == JumpInfo("1", "d-2026-06-11")
        assert overlays[app.screen.get_offset(app.query_one("#one", Static))] == (
            JumpInfo("o", "one")
        ), "the handed-in rows replaced the widgets instead of joining them"


# ---- the overlay ----


async def test_tab_stays_inside_the_overlay() -> None:
    """Focus must not move under the badges while they are being read."""
    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        base = app.screen
        one = app.query_one("#one", Static)
        one.can_focus = True
        one.focus()
        await pilot.pause()

        app.push_screen(JumpOverlay(Jumper({"one": "o"}, base)))
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()

        assert isinstance(app.screen, JumpOverlay), "the overlay is still up"
        assert base.focused is one, "focus underneath did not move"


async def test_covered_overlay_ignores_keys() -> None:
    """The overlay stays on the stack while something else is in front of it."""
    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        chosen: list[str | Widget | None] = []
        app.push_screen(JumpOverlay(Jumper({"one": "o"}, app.screen)), chosen.append)
        await pilot.pause()
        overlay = showing(app, JumpOverlay)

        app.push_screen(Screen())
        await pilot.pause()
        overlay.post_message(events.Key("o", None))
        await pilot.pause()

        assert chosen == [], "the covered overlay chose a target anyway"


async def test_badges_move_on_resize() -> None:
    """A badge is drawn at an offset, not attached to what it points at."""
    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        base = app.screen
        app.push_screen(JumpOverlay(Jumper({"one": "o", "two": "t"}, base)))
        await pilot.pause()
        overlay = showing(app, JumpOverlay)
        before = dict(overlay.overlays)

        await pilot.resize_terminal(40, 24)
        await pilot.pause()

        assert overlay.overlays != before
        assert overlay.overlays == {
            base.get_offset(app.query_one("#one", Static)): JumpInfo("o", "one"),
            base.get_offset(app.query_one("#two", Named)): JumpInfo("t", "two"),
        }


async def test_disabled_hint_rings_and_greys_out() -> None:
    """A key the screen advertises but cannot run stays on the strip, greyed."""
    app = Bound()
    async with app.run_test(size=(40, 10)) as pilot:
        hint = BindingHint("x", "x", "Mark", "mark", disabled=True)
        await app.screen.mount(hint)
        await pilot.pause()

        assert hint.has_class("-disabled")
        assert hint.binding_enabled is False

        rung = False

        def ring() -> None:
            nonlocal rung
            rung = True

        app.bell = ring  # type: ignore[method-assign]
        hint.on_mouse_down()

        assert rung, "a disabled key answers rather than doing nothing at all"
        assert app.marked is False, "and does not run the action it advertises"


async def test_hint_without_description_draws_key() -> None:
    """An assembled empty description still spends the padding around it."""
    app = Bound()
    async with app.run_test(size=(40, 10)) as pilot:
        hint = BindingHint("p", "^p", "", "command_palette")
        await app.screen.mount(hint)
        await pilot.pause()

        assert str(hint.render()) == "^p"


async def test_footer_recomposes_only_when_focused() -> None:
    """Textual publishes the binding map to background applications too."""
    app = Bound()
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        strip = app.query_one(KeyStrip)
        strip.bindings_ready = False
        app.app_focus = False

        strip.bindings_changed(app.screen)

        assert strip.bindings_ready is True, "the map is still recorded"

        scheduled: list[object] = []

        def record(callback: object, *_args: object, **_kwargs: object) -> bool:
            scheduled.append(callback)
            return True

        strip.call_after_refresh = record  # type: ignore[method-assign]

        app.app_focus = True
        strip.bindings_changed(Screen())

        assert scheduled == [], "a map published for another screen is not ours"

        strip.bindings_changed(app.screen)

        assert scheduled == [strip.recompose], "and the one for ours is"
