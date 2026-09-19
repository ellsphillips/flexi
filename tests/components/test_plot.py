"""The plot widget: what it refuses, and what it does when there is no room.

The geometry lives in `tests/domain/test_plot.py`. Here is the part that needs
a stylesheet and a size: tones resolving to colours, and the furniture giving
way as the panel narrows.
"""

from __future__ import annotations

import pytest

from flexi.components.plot import AXIS_WIDTH, PLOT_TONES, Plot
from flexi.domain.plot import Mark, Series
from tests.components.test_common import mounted

BANDS = (
    Series("annual", (2.0, 3.0), Mark.BAR, "annual"),
    Series("sick", (1.0, 1.0), Mark.BAR, "sick"),
)


async def test_unknown_tone_is_refused() -> None:
    """An unknown tone resolves to the widget's own style.

    On this ground that draws the series in the background colour.
    """
    plot = Plot()
    async with mounted(plot):
        with pytest.raises(ValueError, match="No plot tone named puce"):
            plot.show([Series("a", (1.0,), Mark.BAR, "puce")])


async def test_every_declared_tone_resolves_to_a_colour() -> None:
    """The closed set and the stylesheet have to agree."""
    plot = Plot()
    async with mounted(plot):
        for tone in PLOT_TONES:
            assert plot.get_component_styles(f"plot--{tone}").color is not None


async def test_empty_plot_shows_its_message() -> None:
    """A blank panel reads as a chart that failed to render."""
    plot = Plot()
    async with mounted(plot) as pilot:
        plot.show([], empty_message="Not started")
        await pilot.pause()

        assert str(plot.render()) == "Not started"


async def test_single_series_carries_no_legend() -> None:
    """The panel title names it, and a one-line legend is a wasted row."""
    plot = Plot()
    async with mounted(plot):
        plot.show([Series("worked", (1.0, 2.0))])

        assert plot.legend().plain == ""


async def test_narrow_legend_drops_the_names() -> None:
    """Marks alone still map every band to its colour.

    Truncation would silently lose whichever series was listed last.
    """
    plot = Plot()
    async with mounted(plot) as pilot:
        plot.show(BANDS, stacked=True)
        await pilot.pause()
        assert "annual" in plot.legend().plain

        plot.styles.width = AXIS_WIDTH + 6
        await pilot.pause()

        assert "annual" not in plot.legend().plain
        assert plot.legend().plain.strip() == "██ ██"


async def test_panel_too_narrow_shows_its_message() -> None:
    """`AXIS_WIDTH` columns go to the axis before a single bar is drawn."""
    plot = Plot()
    async with mounted(plot) as pilot:
        plot.show(BANDS, stacked=True, empty_message="No room")
        await pilot.pause()
        assert str(plot.render()) != "No room"

        plot.styles.width = AXIS_WIDTH + 1
        await pilot.pause()

        assert str(plot.render()) == "No room"
