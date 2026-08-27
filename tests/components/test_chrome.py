"""The frame around the modules: the nav bar, the status line, and jump mode.

Driven through bare harness applications rather than through Flexi. Each of
these widgets is deliberately ignorant of the application it frames — the header
reaches for the app with ``getattr`` precisely so it can be mounted anywhere —
and the states worth checking here are the ones the real screens are careful
never to be in: a header written to before it is on screen, a status pushed at a
footer that does not exist yet, an overlay that is no longer in front.
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

WIDE = (80, 24)


class Framed(App[None]):
    """A header on its own, with no Flexi behind it."""

    def compose(self) -> ComposeResult:
        yield AppHeader()


class Named(Static):
    """A widget that carries its own jump key rather than being registered."""

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


# -- the nav bar -------------------------------------------------------------


async def test_the_nav_bar_moves_its_highlight_to_the_screen_in_front() -> None:
    """Exactly one destination is teal, and it is the one you are looking at.

    The bar is composed once and lives on every screen, so the highlight has to
    be moved rather than rebuilt; leaving the old one lit is how a nav bar comes
    to claim you are on two screens at once.
    """
    app = Framed()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert active_labels(app) == {"dashboard"}

        app.query_one(AppHeader).set_active("leave")
        await pilot.pause()

        assert active_labels(app) == {"leave"}


# -- the key strip ------------------------------------------------------------


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


async def test_a_binding_hint_matches_its_measurement_and_runs_its_key() -> None:
    """The local public widget keeps the rendering and click contract it replaces."""
    app = Bound()
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        await pilot.pause()
        hint = app.query_one(BindingHint)

        assert str(hint.render()) == "x Mark"
        assert hint.region.width + hint.styles.margin.right == footer_key_cost(
            "x", "Mark"
        )

        await pilot.click(hint)
        await pilot.pause()
        assert app.marked


# -- writing to chrome that is not there yet ---------------------------------


def test_a_header_told_its_context_before_it_is_mounted_does_not_raise() -> None:
    """The app pushes the date and period at every header it can find.

    A header on a screen that has been constructed but not yet mounted has no
    children to write into, and a watcher that queried for one regardless would
    take the screen change down with it.
    """
    header = AppHeader()
    header.context = "Thu 11 Jun · This week"
    assert header.context == "Thu 11 Jun · This week"


async def test_a_status_pushed_at_a_footer_that_is_not_mounted_is_dropped() -> None:
    """Every service result comes through here, including ones raised at startup.

    A message arriving before the footer exists is worth losing; it is not worth
    an exception on a code path that only ever reports on somebody else's work.
    And losing it has to mean losing it — a message held over and painted when
    the bar finally arrives would report a stale startup result over whatever the
    user has since done.
    """
    bar = StatusBar()
    bar.set_status("Clocked in at 09:12", Tone.OK, pill="on the clock")

    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await app.mount(bar)
        await pilot.pause()

        assert str(bar.query_one("#status-message", Static).render()) == ""
        assert str(bar.query_one("#status-pill", Pill).render()) == ""


# -- jump targets ------------------------------------------------------------


async def test_a_widget_may_name_its_own_jump_key() -> None:
    """The alternative is a registry that has to be edited from two places.

    A panel that knows which key reaches it cannot fall out of step with the map
    of targets, because it is the map.
    """
    app = Jumpy()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        overlays = Jumper({"one": "o"}, app.screen).get_overlays()

        assert {info.key for info in overlays.values()} == {"o", "p"}
        assert overlays[app.screen.get_offset(app.query_one(Named))] == JumpInfo(
            "p", app.query_one(Named)
        )


async def test_a_target_that_is_not_a_widget_is_added_by_the_screen() -> None:
    """A table row has no id and no rectangle, so it cannot be walked to.

    The screen computes those offsets itself and hands them over, which is what
    lets the day rows carry badges without becoming widgets.
    """
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


# -- the overlay -------------------------------------------------------------


async def test_tab_does_not_leak_out_of_the_overlay() -> None:
    """Focus must not move under the badges while they are being read.

    A tab that reached the screen below would shift focus after the jump had
    already landed, which reads as the jump having gone to the wrong place.
    """
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


async def test_a_key_arriving_after_the_overlay_is_covered_does_not_jump() -> None:
    """The overlay stays on the stack while something else is in front of it.

    A queued keypress delivered then would dismiss a screen the user is no
    longer looking at, and hand back a target they never chose.
    """
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


async def test_the_badges_follow_the_layout_when_the_terminal_resizes() -> None:
    """A badge is drawn at an offset, not attached to what it points at.

    Every offset is stale the moment the compositor moves anything, so a badge
    left where it was would be pointing at whatever has since slid under it.
    """
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


async def test_a_disabled_hint_looks_unavailable_and_rings_instead_of_acting() -> None:
    """A key the screen advertises but cannot run at this moment.

    It stays on the strip rather than vanishing -- a footer that reshuffles as
    state changes is harder to read than one with a greyed key on it -- so it
    has to carry the class that greys it and refuse to fire the action.
    """
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


async def test_a_hint_with_no_description_draws_the_key_alone() -> None:
    """The command palette's key carries no words on a narrow strip.

    Assembling an empty description would still spend the padding around it, so
    the key would sit a column left of where the measurement said it would.
    """
    app = Bound()
    async with app.run_test(size=(40, 10)) as pilot:
        hint = BindingHint("p", "^p", "", "command_palette")
        await app.screen.mount(hint)
        await pilot.pause()

        assert str(hint.render()) == "^p"


async def test_a_footer_recomposes_only_while_the_terminal_has_focus() -> None:
    """Textual publishes the binding map to background applications too.

    Recomposing then costs a layout pass for a strip nobody is looking at, and
    on a tiling desktop that is every focus change in the session.
    """
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
