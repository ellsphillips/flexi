"""The rails under the header: how far through the day, and the period.

A rail is asked directly rather than through the dashboard. The arithmetic and
the trimming are the whole widget, and the states worth checking — a day that
expects nothing, a terminal with no room for a bar — are ones the seeded
dashboard never happens to be in.
"""

from __future__ import annotations

from datetime import timedelta

from textual.app import App, ComposeResult

from flexi.components.common import TRACK
from flexi.components.progress import ProgressRail

NARROW = (20, 6)
"""Room for the label and the figures and nothing else."""

WIDE = (60, 6)


class Rail(App[None]):
    """One rail, so it has a width and the component styles it draws with."""

    def compose(self) -> ComposeResult:
        yield ProgressRail("TODAY", id="rail-day")


def test_a_day_that_expects_nothing_is_complete_rather_than_undefined() -> None:
    """A share of nothing has no answer, and the rail still has to draw.

    A Sunday, a bank holiday and a day of booked leave all expect nothing, so
    this is not a corner: it is two days in seven. An hour worked against no
    expectation is the whole of what was asked for, and no hours at all is none
    of it.
    """
    rail = ProgressRail("TODAY")

    rail.show(timedelta(hours=1), timedelta())
    assert rail.share == 1.0

    rail.show(timedelta(), timedelta())
    assert rail.share == 0.0


async def test_a_rail_with_nothing_expected_shows_the_hours_it_has() -> None:
    """A readout of "0:45 of 0:00" says nothing that "0:45" does not say better.

    The second one also says the thing worth knowing about a Sunday, which is
    that the hours on it were not owed to anybody.
    """
    app = Rail()
    async with app.run_test(size=WIDE) as pilot:
        rail = app.query_one(ProgressRail)

        rail.show(timedelta(minutes=45), timedelta())
        await pilot.pause()
        assert str(rail.render()).endswith("0:45")

        rail.show(timedelta(), timedelta())
        await pilot.pause()
        assert str(rail.render()).endswith("—")


async def test_a_compact_rail_gives_up_its_figures_before_its_bar() -> None:
    """Under the header there is room for a bar or for two durations, not both.

    The bar is the thing that can be read without reading, so the pair of
    durations is what goes, and a percentage stands in for them.
    """
    app = Rail()
    async with app.run_test(size=WIDE) as pilot:
        rail = app.query_one(ProgressRail)
        rail.show(timedelta(minutes=222), timedelta(minutes=444), compact=True)
        await pilot.pause()

        drawn = str(rail.render())
        assert drawn.endswith("50%")
        assert TRACK in drawn


async def test_a_rail_too_narrow_for_a_bar_keeps_the_figures() -> None:
    """The figures are the answer; the bar is the gloss on it.

    A track squeezed into three cells cannot show a share to a resolution
    anybody could read, and it takes those cells from the only part of the rail
    that still means something at that width.
    """
    app = Rail()
    async with app.run_test(size=NARROW) as pilot:
        rail = app.query_one(ProgressRail)
        rail.show(timedelta(hours=1), timedelta(hours=2))
        await pilot.pause()

        drawn = str(rail.render())
        assert drawn == "TODAY 1:00 of 2:00"
        assert TRACK not in drawn
