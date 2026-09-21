"""Feature 6: the keyboard, as a set of rules.

These tests discover what they check by walking the application, so a binding
or a modal written next week is covered the day it is written.
"""

from __future__ import annotations

import inspect
import pkgutil
from importlib import import_module
from typing import Any

import pytest
from textual.binding import Binding

import flexi.screens
from flexi.components.chrome import NavItemLabel, footer_key_cost, keys_that_fit
from flexi.components.expandable import ExpandableTable
from flexi.screens.help import HelpScreen, collect_bindings, declared_by_flexi
from flexi.screens.insights import InsightsScreen
from flexi.screens.modals import FlexiModal
from tests.conftest import settled
from tests.tui.conftest import WIDE, AppFactory, showing


def modal_classes() -> list[type[FlexiModal[Any]]]:
    """Find every modal in the package by walking it."""
    found: list[type[FlexiModal[Any]]] = []
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


# ---- bindings ----


async def test_no_two_shown_bindings_share_a_key(app_factory: AppFactory) -> None:
    """One key is never advertised for two actions on one screen."""
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


async def test_every_binding_names_an_action_that_exists(
    app_factory: AppFactory,
) -> None:
    """A typo in an action name is otherwise silent until the key is pressed."""
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


async def test_key_strip_says_how_many_it_dropped(app_factory: AppFactory) -> None:
    """The last columns hold a count of what was dropped, not half a key."""
    app = app_factory()
    async with app.run_test(size=(64, 24)) as pilot:
        await pilot.pause()
        # The strip is recomposed after the first refresh, so what it says is
        # not settled until that has landed.
        await settled(pilot)
        text = " ".join(
            str(widget.render()) for widget in app.screen.query("OverflowLabel")
        )
        assert "more" in text


def test_entry_costs_two_strings_and_a_margin() -> None:
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
def test_strip_reserves_room_for_its_notice(
    costs: list[int], budget: int, marker: int, shown: int
) -> None:
    assert keys_that_fit(costs, budget, marker) == shown


# ---- modals ----


def test_there_are_modals_to_check() -> None:
    """Fails if the discovery above stops finding anything."""
    assert len(modal_classes()) >= 3


@pytest.mark.parametrize("modal", modal_classes(), ids=lambda cls: cls.__name__)
def test_every_modal_binds_escape_and_enter(modal: type[FlexiModal[Any]]) -> None:
    keys = {
        binding.key if isinstance(binding, Binding) else binding[0]
        for binding in modal.BINDINGS
    }
    assert "escape" in keys
    assert "enter" in keys


# ---- the pointer ----


async def test_clicking_a_tab_navigates(app_factory: AppFactory) -> None:
    """Every nav item is a widget with a hover state, so a pointer works.

    `NavItemLabel` posts `NavBar.Selected`, and the keys and the pointer have
    to arrive at the same place.
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
        opened = showing(app, InsightsScreen)

        dashboard = next(
            label
            for label in opened.query(NavItemLabel)
            if label.item.screen == "dashboard"
        )
        await pilot.click(dashboard)
        await pilot.pause()
        assert not isinstance(app.screen, InsightsScreen)


async def test_active_tab_is_marked(app_factory: AppFactory) -> None:
    """The bar says where you are, not only where you can go."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        active = [
            label.item.screen
            for label in app.screen.query(NavItemLabel)
            if label.has_class("-active")
        ]
        assert active == ["dashboard"]


# ---- help ----


async def test_question_mark_lists_flexi_bindings_only(app_factory: AppFactory) -> None:
    """Flexi's own bindings, not Textual's scroll bindings."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)

        groups = collect_bindings(app.screen_stack[-2])
        assert "Anywhere" in groups
        assert "VerticalScroll" not in groups
        actions = [
            description for entries in groups.values() for _, description in entries
        ]
        assert "Clock" in actions
        assert "Scroll Up" not in actions


async def test_help_closes_on_escape(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_help_leaves_out_the_keys_the_widget_only_inherited(
    app_factory: AppFactory,
) -> None:
    """A Flexi table is still a `DataTable`, and the filter asks the widget."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.screen.query_one("#records-table", ExpandableTable).focus()
        await pilot.pause()

        groups = collect_bindings(app.screen)
        descriptions = {
            description for entries in groups.values() for _, description in entries
        }
        assert "Book absence" in descriptions, "Flexi's own are still listed"
        for inherited in (
            "Cursor Left",
            "Page Left",
            "Page Right",
            "Focus Next",
            "Copy selected text",
        ):
            assert inherited not in descriptions


async def test_help_lists_every_key_an_action_answers_to(
    app_factory: AppFactory,
) -> None:
    """`left,h` is one binding in the source and two in `active_bindings`."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f2")
        await pilot.pause()

        groups = collect_bindings(app.screen)
        calendar = dict(groups["Leave calendar"])
        assert "← / h" in calendar
        assert calendar["← / h"] == "Back a day"
        assert {"↓ / j", "↑ / k", "→ / l"} <= set(calendar)


def test_binding_no_class_declares_is_unowned() -> None:
    """The walk falls off the end of the MRO for a node with no bindings at all."""
    assert not declared_by_flexi(object(), Binding("x", "nothing", "Nothing"))
