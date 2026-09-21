"""The public surface of Flexi's dependency-free functional core."""

from __future__ import annotations

from pathlib import Path
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
    plot,
    punch,
    wallet,
)
from flexi.domain import stitch as stitch_module
from tests.public_api import check_declared_api, check_public_annotations

LEAF_MODULES = (
    balance,
    dates,
    formatting,
    leaveyear,
    ledger,
    period,
    plot,
    punch,
    stitch_module,
    wallet,
)


@pytest.mark.parametrize("module", LEAF_MODULES, ids=lambda module: module.__name__)
def test_every_domain_module_declares_an_immutable_api(module: ModuleType) -> None:
    check_declared_api(module)


def test_facade_resolves_ambiguous_leaf_names() -> None:
    """Two leaves each export a ``Cell`` and a ``ZERO``, so the facade renames them."""
    assert domain.PunchCell is punch.Cell
    assert domain.CalendarCell is stitch_module.Cell
    assert domain.ZERO_DURATION is balance.ZERO
    assert domain.ZERO_TEXT is formatting.ZERO
    assert domain.plot is plot
    assert domain.plot_series is plot.plot


def test_leaf_list_covers_the_package() -> None:
    """A leaf missing from ``LEAF_MODULES`` is held to none of the rules above."""
    package = Path(domain.__file__ or "").parent
    leaves = {
        path.stem for path in package.glob("*.py") if not path.stem.startswith("_")
    }
    published = {
        value.__name__
        for value in (getattr(domain, name) for name in domain.__all__)
        if isinstance(value, ModuleType)
    }

    assert leaves == {module.__name__.rpartition(".")[2] for module in LEAF_MODULES}
    assert {module.__name__ for module in LEAF_MODULES} <= published


def test_facade_publishes_the_date_helpers() -> None:
    assert domain.add_days is dates.add_days
    assert domain.month_index is dates.month_index
    assert domain.resolve_month_day is dates.resolve_month_day


@pytest.mark.parametrize("module", LEAF_MODULES, ids=lambda module: module.__name__)
def test_public_annotations_resolve_without_any(module: ModuleType) -> None:
    check_public_annotations(module)


def test_imported_implementation_dependencies_are_not_public() -> None:
    assert {"Iterable", "dataclass", "datetime", "timedelta"}.isdisjoint(
        balance.__all__
    )
