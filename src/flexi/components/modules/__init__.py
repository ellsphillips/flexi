"""Lazy public facade for the dashboard's composable modules.

The leaf modules remain available for precise imports. This facade gives
extension code one discoverable namespace without making every dashboard
module load when a caller asks for only the shared :class:`Module` contract.
"""

from __future__ import annotations

# These imports describe attributes that PEP 562 resolves lazily at runtime.
# ruff: noqa: TC004
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from flexi.components.modules import (
        balance,
        base,
        clock,
        monthview,
        records,
        wallet,
    )
    from flexi.components.modules.balance import (
        STATE_CLASSES,
        BalanceModule,
        lean_class,
    )
    from flexi.components.modules.base import Module
    from flexi.components.modules.clock import ClockModule
    from flexi.components.modules.monthview import (
        KIND_CLASSES,
        WEEKS,
        MonthView,
        cell_classes,
        cell_text,
        month_grid,
    )
    from flexi.components.modules.records import (
        BADGE_WIDTH,
        BRANCH,
        CELL_PADDING,
        COLUMNS,
        FIXED_COLUMNS,
        LAST,
        MAX_JUMP_ROWS,
        STRIP_WIDTH_FLOOR,
        BookHere,
        DeleteHere,
        RecordsModule,
        totals_subtitle,
    )
    from flexi.components.modules.wallet import TRACKED, BookRequested, WalletModule

_MODULE_EXPORTS: Final = MappingProxyType(
    {
        "balance": ("STATE_CLASSES", "BalanceModule", "lean_class"),
        "base": ("Module",),
        "clock": ("ClockModule",),
        "monthview": (
            "KIND_CLASSES",
            "WEEKS",
            "MonthView",
            "cell_classes",
            "cell_text",
            "month_grid",
        ),
        "records": (
            "BADGE_WIDTH",
            "BRANCH",
            "CELL_PADDING",
            "COLUMNS",
            "FIXED_COLUMNS",
            "LAST",
            "MAX_JUMP_ROWS",
            "STRIP_WIDTH_FLOOR",
            "BookHere",
            "DeleteHere",
            "RecordsModule",
            "totals_subtitle",
        ),
        "wallet": ("TRACKED", "BookRequested", "WalletModule"),
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
    "balance",
    "base",
    "clock",
    "monthview",
    "records",
    "wallet",
    "STATE_CLASSES",
    "BalanceModule",
    "lean_class",
    "Module",
    "ClockModule",
    "KIND_CLASSES",
    "WEEKS",
    "MonthView",
    "cell_classes",
    "cell_text",
    "month_grid",
    "BADGE_WIDTH",
    "BRANCH",
    "CELL_PADDING",
    "COLUMNS",
    "FIXED_COLUMNS",
    "LAST",
    "MAX_JUMP_ROWS",
    "STRIP_WIDTH_FLOOR",
    "BookHere",
    "DeleteHere",
    "RecordsModule",
    "totals_subtitle",
    "TRACKED",
    "BookRequested",
    "WalletModule",
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
