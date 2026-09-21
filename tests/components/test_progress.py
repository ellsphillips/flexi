"""The rails under the header: how far through the day, and through the period."""

from __future__ import annotations

from datetime import timedelta

from textual.app import App, ComposeResult

from flexi.components.common import TRACK
from flexi.components.progress import ProgressRail

NARROW = (20, 6)
"""Room for the label and the figures and nothing else."""

WIDE = (60, 6)


class Rail(App[None]):
    """A single rail, mounted so it has a width and its component styles."""

    def compose(self) -> ComposeResult:
        yield ProgressRail("TODAY", id="rail-day")


def test_zero_expected_still_has_a_share() -> None:
    """An hour against no expectation is all of it; no hours at all is none."""
    rail = ProgressRail("TODAY")

    rail.show(timedelta(hours=1), timedelta())
    assert rail.share == 1.0

    rail.show(timedelta(), timedelta())
    assert rail.share == 0.0


async def test_zero_expected_shows_the_hours_alone() -> None:
    app = Rail()
    async with app.run_test(size=WIDE) as pilot:
        rail = app.query_one(ProgressRail)

        rail.show(timedelta(minutes=45), timedelta())
        await pilot.pause()
        assert str(rail.render()).endswith("0:45")

        rail.show(timedelta(), timedelta())
        await pilot.pause()
        assert str(rail.render()).endswith("—")


async def test_compact_rail_keeps_the_bar_and_a_percentage() -> None:
    app = Rail()
    async with app.run_test(size=WIDE) as pilot:
        rail = app.query_one(ProgressRail)
        rail.show(timedelta(minutes=222), timedelta(minutes=444), compact=True)
        await pilot.pause()

        drawn = str(rail.render())
        assert drawn.endswith("50%")
        assert TRACK in drawn


async def test_narrow_rail_drops_the_bar_for_the_figures() -> None:
    """A track squeezed into three cells cannot show a share anyone can read."""
    app = Rail()
    async with app.run_test(size=NARROW) as pilot:
        rail = app.query_one(ProgressRail)
        rail.show(timedelta(hours=1), timedelta(hours=2))
        await pilot.pause()

        drawn = str(rail.render())
        assert drawn == "TODAY 1:00 of 2:00"
        assert TRACK not in drawn
