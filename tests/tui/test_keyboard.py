"""Feature 6: the keyboard experience, as a set of rules rather than a list.

These tests discover what they check by walking the application, so a binding or
a modal written next week is covered the day it is written rather than the day
somebody remembers to add a test for it.
"""

from __future__ import annotations

import inspect
import pkgutil
from importlib import import_module

import pytest

import flexi.screens
from flexi.components.chrome import NavItemLabel, footer_key_cost, keys_that_fit
from flexi.screens.help import HelpScreen, collect_bindings
from flexi.screens.insights import InsightsScreen
from flexi.screens.modals import FlexiModal
from tests.tui.conftest import WIDE

pytestmark = pytest.mark.usefixtures("_frozen")


def modal_classes() -> list[type[FlexiModal]]:
    """Every modal in the package, found by walking it."""
    found: list[type[FlexiModal]] = []
    for info in pkgutil.walk_packages(flexi.screens.__path__, "flexi.screens."):
        module = import_module(info.name)
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, FlexiModal)
                and obj is not FlexiModal
                and obj.__module__ == module.__name__
                and obj not in found
            ):
                found.append(obj)
    return found


# -- bindings --------------------------------------------------------------


async def test_no_two_shown_bindings_share_a_key(app_factory) -> None:
    """It never advertises one key doing two things on the same screen."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        seen: dict[str, str] = {}
        for _node, binding, _enabled, _tooltip in app.screen.active_bindings.values():
            if not binding.show:
                continue
            clash = seen.get(binding.key)
            assert clash is None or clash == binding.action, (
                f"{binding.key!r} is shown for both {clash!r} and {binding.action!r}"
            )
            seen[binding.key] = binding.action


async def test_every_binding_names_an_action_that_exists(app_factory) -> None:
    """It fails here rather than doing nothing when the key is pressed.

    A typo in an action name is otherwise silent until a user presses the key and
    finds that it does nothing at all.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for node, binding, _enabled, _tooltip in app.screen.active_bindings.values():
            if not type(node).__module__.startswith("flexi."):
                continue  # Textual's own bindings are its problem, not ours
            name = binding.action.split("(")[0].strip()
            if "." in name:
                continue  # a namespaced action like `app.focus_next`
            assert hasattr(node, f"action_{name}"), (
                f"{type(node).__name__} binds {binding.key!r} to a missing "
                f"action_{name}"
            )


async def test_the_key_strip_says_how_many_it_dropped(app_factory) -> None:
    """It spends its last columns on a pointer rather than half a key."""
    app = app_factory()
    async with app.run_test(size=(64, 24)) as pilot:
        await pilot.pause()
        text = " ".join(
            str(widget.render()) for widget in app.screen.query("OverflowLabel")
        )
        assert "more" in text


def test_an_entry_costs_its_two_strings_plus_its_margin() -> None:
    """The formula the strip measures with, checked rather than trusted."""
    assert footer_key_cost("^q", "Quit") == 8


@pytest.mark.parametrize(
    ("costs", "budget", "marker", "shown"),
    [
        ([5, 5, 5], 20, 8, 3),  # everything fits; the marker costs nothing
        ([5, 5, 5], 14, 8, 3),  # 15 costs, less the last margin, is exactly 14
        ([5, 5, 5], 13, 8, 1),  # one entry plus the notice fits; two do not
        ([5, 5, 5], 10, 8, 0),  # not even one entry survives beside the notice
    ],
)
def test_the_strip_reserves_room_for_its_own_overflow_notice(
    costs, budget, marker, shown
) -> None:
    """A strip that overflowed *and* hid the fact is the failure to prevent."""
    assert keys_that_fit(costs, budget, marker) == shown


# -- modals ----------------------------------------------------------------


def test_there_are_modals_to_check() -> None:
    """It fails loudly if the discovery above stops finding anything."""
    assert len(modal_classes()) >= 3


@pytest.mark.parametrize("modal", modal_classes(), ids=lambda cls: cls.__name__)
def test_every_modal_binds_escape_and_enter(modal: type[FlexiModal]) -> None:
    """It keeps the contract every dialog in the application keeps."""
    keys = {binding.key for binding in modal.BINDINGS}
    assert "escape" in keys
    assert "enter" in keys


# -- the pointer -----------------------------------------------------------


async def test_clicking_a_tab_navigates(app_factory) -> None:
    """Every nav item is a widget with a hover state so a pointer works.

    It did not, for a while: `NavItemLabel` posted `NavBar.Selected` and nothing
    listened, so the tabs looked clickable and were not. The keys and the
    pointer have to arrive at the same place.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        insights = next(
            label
            for label in app.screen.query(NavItemLabel)
            if label.item.screen == "insights"
        )
        await pilot.click(insights)
        await pilot.pause()
        assert isinstance(app.screen, InsightsScreen)

        dashboard = next(
            label
            for label in app.screen.query(NavItemLabel)
            if label.item.screen == "dashboard"
        )
        await pilot.click(dashboard)
        await pilot.pause()
        assert not isinstance(app.screen, InsightsScreen)


async def test_the_active_tab_is_marked(app_factory) -> None:
    """It says where you are, not only where you can go."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        active = [
            label.item.screen
            for label in app.screen.query(NavItemLabel)
            if label.has_class("-active")
        ]
        assert active == ["dashboard"]


# -- help ------------------------------------------------------------------


async def test_question_mark_lists_flexi_bindings_only(app_factory) -> None:
    """It shows what Flexi added, not Textual's eight scroll bindings."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)

        groups = collect_bindings(app.screen_stack[-2])
        assert "Anywhere" in groups
        assert "VerticalScroll" not in groups
        actions = [description for entries in groups.values() for _, description in entries]
        assert "Clock" in actions
        assert "Scroll Up" not in actions


async def test_help_closes_on_escape(app_factory) -> None:
    """It leaves the way every other dialog does."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
