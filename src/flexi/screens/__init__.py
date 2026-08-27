"""Typed, lazy access to Flexi's screens and modal contracts.

The facade keeps every deep path stable while making the complete screen API
discoverable from one namespace. Resolution is lazy so importing one screen
does not instantiate the dependencies of every other destination.
"""

from __future__ import annotations

# These imports describe attributes that PEP 562 resolves lazily at runtime.
# ruff: noqa: TC004
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from flexi.screens import (
        dashboard,
        help,  # noqa: A004 - the supported module name is intentionally direct
        insights,
        leave,
        modals,
        settings,
        setup,
    )
    from flexi.screens.dashboard import JUMP_TARGETS, DashboardScreen, with_time
    from flexi.screens.help import (
        HelpScreen,
        collect_bindings,
        declared_by_flexi,
        label_for,
    )
    from flexi.screens.insights import (
        RIBBON_DAYS,
        BalanceHistory,
        InsightsScreen,
        LeaveBurndown,
        ShapeOfTheWeeks,
        WhereTheHoursWent,
        YearAtAGlance,
    )
    from flexi.screens.leave import (
        PORTION_CYCLE,
        REMOVE_THRESHOLD,
        SIDEBAR,
        LeaveScreen,
        nothing_doing,
        preview,
    )
    from flexi.screens.modals import (
        AbsenceBooking,
        AbsenceModal,
        ConfirmModal,
        FlexiModal,
        GoToDateModal,
        selected_name,
    )
    from flexi.screens.settings import (
        ALL_REQUIRED,
        NO_DIVISION,
        SettingsScreen,
        parse_answers,
    )
    from flexi.screens.setup import (
        ASK_WIDTH,
        FIELD_WIDTH,
        FORM_WIDTH,
        GUTTER,
        HEADING_ROWS,
        NOTE_WIDTH,
        QUESTION_ROWS,
        RAIL_WIDTH,
        RISE,
        SLIDE,
        TAIL_ROWS,
        Question,
        Rail,
        SetupScreen,
        form_rows,
        sized,
    )

_MODULE_EXPORTS: Final = MappingProxyType(
    {
        "dashboard": ("JUMP_TARGETS", "DashboardScreen", "with_time"),
        "help": ("HelpScreen", "collect_bindings", "declared_by_flexi", "label_for"),
        "insights": (
            "RIBBON_DAYS",
            "BalanceHistory",
            "InsightsScreen",
            "LeaveBurndown",
            "ShapeOfTheWeeks",
            "WhereTheHoursWent",
            "YearAtAGlance",
        ),
        "leave": (
            "PORTION_CYCLE",
            "REMOVE_THRESHOLD",
            "SIDEBAR",
            "LeaveScreen",
            "nothing_doing",
            "preview",
        ),
        "modals": (
            "AbsenceBooking",
            "AbsenceModal",
            "ConfirmModal",
            "FlexiModal",
            "GoToDateModal",
            "selected_name",
        ),
        "settings": (
            "ALL_REQUIRED",
            "NO_DIVISION",
            "SettingsScreen",
            "parse_answers",
        ),
        "setup": (
            "ASK_WIDTH",
            "FIELD_WIDTH",
            "FORM_WIDTH",
            "GUTTER",
            "HEADING_ROWS",
            "NOTE_WIDTH",
            "QUESTION_ROWS",
            "RAIL_WIDTH",
            "RISE",
            "SLIDE",
            "TAIL_ROWS",
            "Question",
            "Rail",
            "SetupScreen",
            "form_rows",
            "sized",
        ),
    }
)

_EXPORTS: Final = MappingProxyType(
    {
        public_name: (module_name, public_name)
        for module_name, public_names in _MODULE_EXPORTS.items()
        for public_name in public_names
    }
)

# Keep the public surface grouped by its defining module.
__all__ = (  # noqa: RUF022
    "dashboard",
    "help",
    "insights",
    "leave",
    "modals",
    "settings",
    "setup",
    "JUMP_TARGETS",
    "DashboardScreen",
    "with_time",
    "HelpScreen",
    "collect_bindings",
    "declared_by_flexi",
    "label_for",
    "RIBBON_DAYS",
    "BalanceHistory",
    "InsightsScreen",
    "LeaveBurndown",
    "ShapeOfTheWeeks",
    "WhereTheHoursWent",
    "YearAtAGlance",
    "PORTION_CYCLE",
    "REMOVE_THRESHOLD",
    "SIDEBAR",
    "LeaveScreen",
    "nothing_doing",
    "preview",
    "AbsenceBooking",
    "AbsenceModal",
    "ConfirmModal",
    "FlexiModal",
    "GoToDateModal",
    "selected_name",
    "ALL_REQUIRED",
    "NO_DIVISION",
    "SettingsScreen",
    "parse_answers",
    "ASK_WIDTH",
    "FIELD_WIDTH",
    "FORM_WIDTH",
    "GUTTER",
    "HEADING_ROWS",
    "NOTE_WIDTH",
    "QUESTION_ROWS",
    "RAIL_WIDTH",
    "RISE",
    "SLIDE",
    "TAIL_ROWS",
    "Question",
    "Rail",
    "SetupScreen",
    "form_rows",
    "sized",
)


def __getattr__(name: str) -> object:
    if name in _MODULE_EXPORTS:
        module_name, source_name = f"{__name__}.{name}", None
    elif route := _EXPORTS.get(name):
        module, source_name = route
        module_name = f"{__name__}.{module}"
    else:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    from importlib import import_module

    imported = import_module(module_name)
    value = imported if source_name is None else getattr(imported, source_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include unresolved facade exports in interactive discovery."""
    return sorted(set(globals()) | set(__all__))
