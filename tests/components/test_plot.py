"""The plot widget: what it refuses, and what it does when there is no room.

The geometry is tested in `tests/domain/test_plot.py`. What is left here is the
part that needs a stylesheet and a size -- tones resolving to colours, and the
furniture giving way as the panel narrows.
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


async def test_a_tone_the_stylesheet_has_never_heard_of_is_refused() -> None:
    """An unknown tone resolves to the widget's own style.

    On this ground that is a series drawn in the background colour: present in
    every measurement, absent from the picture, and impossible to notice from
    the code that asked for it. Refusing it names the mistake instead.
    """
    plot = Plot()
    async with mounted(plot):
        with pytest.raises(ValueError, match="No plot tone named puce"):
            plot.show([Series("a", (1.0,), Mark.BAR, "puce")])


async def test_every_declared_tone_resolves_to_a_colour() -> None:
    """The closed set and the stylesheet have to agree, or the check is theatre."""
    plot = Plot()
    async with mounted(plot):
        for tone in PLOT_TONES:
            assert plot.get_component_styles(f"plot--{tone}").color is not None


async def test_an_empty_plot_says_so_rather_than_drawing_an_empty_box() -> None:
    """A blank panel reads as a chart that failed to render."""
    plot = Plot()
    async with mounted(plot) as pilot:
        plot.show([], empty_message="Not started")
        await pilot.pause()

        assert str(plot.render()) == "Not started"


async def test_a_single_series_carries_no_legend() -> None:
    """The panel title names it, and a legend under one line is a wasted row."""
    plot = Plot()
    async with mounted(plot):
        plot.show([Series("worked", (1.0, 2.0))])

        assert plot.legend().plain == ""


async def test_a_legend_that_will_not_fit_drops_the_names_not_the_series() -> None:
    """Truncation loses whichever series was listed last, and says nothing.

    Marks alone still map every band to its colour, which is the half of a
    legend that cannot be guessed from the panel title.
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


async def test_a_panel_too_narrow_to_draw_in_says_so_instead() -> None:
    """Six columns go to the axis before a single bar is drawn.

    A plot given eight is a chart made almost entirely of furniture, and two
    columns of bars is a worse answer than a sentence saying there is no room.
    """
    plot = Plot()
    async with mounted(plot) as pilot:
        plot.show(BANDS, stacked=True, empty_message="No room")
        await pilot.pause()
        assert str(plot.render()) != "No room"

        plot.styles.width = AXIS_WIDTH + 1
        await pilot.pause()

        assert str(plot.render()) == "No room"
