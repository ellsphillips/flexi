"""The public surface of Flexi's dependency-free functional core."""

from __future__ import annotations

from types import ModuleType

import pytest

from flexi import domain
from flexi.domain import (
    balance,
    dates,
    formatting,
    leaveyear,
    ledger,
    period,
    punch,
    wallet,
)
from flexi.domain import stitch as stitch_module

LEAF_MODULES = (
    balance,
    dates,
    formatting,
    leaveyear,
    ledger,
    period,
    punch,
    stitch_module,
    wallet,
)


@pytest.mark.parametrize("module", LEAF_MODULES, ids=lambda module: module.__name__)
def test_every_domain_module_declares_an_immutable_api(module: ModuleType) -> None:
    exports: tuple[str, ...] = module.__all__

    assert isinstance(exports, tuple)
    assert len(exports) == len(set(exports))
    assert all(hasattr(module, name) for name in exports)

    namespace: dict[str, object] = {}
    exec(f"from {module.__name__} import *", namespace)  # noqa: S102
    assert set(namespace) - {"__builtins__"} == set(exports)


def test_the_domain_facade_resolves_ambiguous_leaf_names() -> None:
    """A flat API must not make two unrelated ``Cell`` or ``ZERO`` values race."""
    assert domain.PunchCell is punch.Cell
    assert domain.CalendarCell is stitch_module.Cell
    assert domain.ZERO_DURATION is balance.ZERO
    assert domain.ZERO_TEXT is formatting.ZERO


def test_imported_implementation_dependencies_are_not_public() -> None:
    """Wildcard consumers get Flexi's API, not the modules used to build it."""
    assert {"Iterable", "dataclass", "datetime", "timedelta"}.isdisjoint(
        balance.__all__
    )
