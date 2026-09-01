"""The character charts, checked as pictures rather than as numbers.

Every one of these draws a shape and prints the figure beside it, so the tests
ask both questions: is the shape right, and does the caption still say what the
shape means. A chart nobody can read a value off is a mood, and a caption that
disagrees with the bars is worse than either alone.

The widgets are mounted one at a time into an empty app carrying the real
stylesheets -- the ramp is only a ramp if the CSS behind it defines eight
distinct colours, and a bare app would draw every step in the same white.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import PurePath
from typing import ClassVar

import pytest
from rich.console import Console
from rich.text import Text
from textual.app import App, ComposeResult
from textual.pilot import Pilot
from textual.widget import Widget

from flexi import wallclock
from flexi.components.charts import (
    AMENDED_HEAT,
    BASELINE,
    BLOCK,
    DIVERGING_STEPS,
    EMPTY,
    FULL,
    HEAT,
    Burndown,
    Column,
    DivergingBars,
    WeekRibbon,
    YearHeatmap,
    week_columns,
)
from flexi.constants import AbsenceType, DayKind, Portion
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment
from flexi.domain.punch import Window
from flexi.theme import THEME_NAME, THEME_PATH, flexi_theme

PACKAGE = THEME_PATH.parent.parent
CONSOLE = Console()
CONTRACTED = timedelta(hours=7, minutes=24)
MONDAY = date(2025, 6, 2)
NOW = wallclock.local(datetime.combine(MONDAY, time(23, 59)))
"""When these ribbons are drawn. `render_strip` takes the moment rather than
guessing at it, and the end of the day is the reading that draws a closed day
the same however often it is redrawn."""

AMENDED = Segment(
    session_id=1,
    start=wallclock.local(datetime.combine(MONDAY, time(9))),
    end=wallclock.local(datetime.combine(MONDAY, time(17))),
    amended=True,
)
"""A morning nobody punched in for, written up later."""


@asynccontextmanager
async def mounted(widget: Widget, *, width: int = 24) -> AsyncIterator[Pilot[None]]:
    """Run one chart in an app that has the palette and nothing else."""
    widget.styles.width = width
    widget.styles.height = 12

    class Harness(App[None]):
        CSS_PATH: ClassVar[list[str | PurePath]] = [
            PACKAGE / "theme" / "flexi.tcss",
            PACKAGE / "styles" / "dashboard.tcss",
        ]

        def __init__(self) -> None:
            super().__init__()
            self.register_theme(flexi_theme())
            self.theme = THEME_NAME

        def compose(self) -> ComposeResult:
            yield widget

    async with Harness().run_test(size=(80, 24)) as pilot:
        yield pilot


def day(
    when: date,
    *,
    effect: timedelta = timedelta(),
    working: bool = True,
    holiday: str | None = None,
    absences: tuple[AbsenceSlice, ...] = (),
    segments: tuple[Segment, ...] = (),
) -> DayLedger:
    """A ledger whose balance effect is exactly what the test asked for."""
    expected = CONTRACTED if working else timedelta()
    return DayLedger(
        date=when,
        kind=DayKind.WORKING if working else DayKind.WEEKEND,
        is_working_day=working,
        contracted=CONTRACTED,
        worked=expected + effect,
        expected=expected,
        holiday_title=holiday,
        absences=absences,
        segments=segments,
    )


def colours(text: Text) -> list[str]:
    """The colour of every character, as the terminal would paint it."""
    painted: list[str] = []
    for segment in text.render(CONSOLE):
        style = segment.style
        name = style.color.name if style is not None and style.color else ""
        painted.extend([name] * len(segment.text))
    return painted


def lines(widget: Widget) -> list[str]:
    return str(widget.render()).split("\n")


# -- DivergingBars -----------------------------------------------------------


async def test_a_chart_with_no_weeks_in_it_says_so() -> None:
    """An empty panel reads as a widget that failed rather than as no data."""
    chart = DivergingBars()
    async with mounted(chart):
        assert str(chart.render()) == "Nothing recorded yet"


async def test_a_surplus_is_drawn_above_the_line_and_a_deficit_below() -> None:
    """The whole grammar of a diverging chart.

    Which side of the line a bar is on says what it means, so the reader never
    has to look a colour up.
    """
    chart = DivergingBars(height=5)
    async with mounted(chart):
        chart.show([Column("2", 2.0, "+2:00"), Column("9", -2.0, "−2:00")])
        drawn = lines(chart)
        rule = next(index for index, row in enumerate(drawn) if BASELINE in row)
        above = "".join(row[0] for row in drawn[:rule])
        below = "".join(row[0] for row in drawn[rule + 1 : -1])
        assert BLOCK in above
        assert BLOCK not in below
        assert BLOCK in "".join(row[2] for row in drawn[rule + 1 : -1])


async def test_a_run_of_weeks_with_no_deficit_keeps_one_row_for_the_line() -> None:
    """A series with no deficit does not need four rows of empty negative axis."""
    chart = DivergingBars(height=7)
    async with mounted(chart):
        chart.show([Column("2", 3.0, "+3:00"), Column("9", 1.0, "+1:00")])
        drawn = lines(chart)
        rule = next(index for index, row in enumerate(drawn) if BASELINE in row)
        assert rule == 5
        assert len(drawn) == rule + 3


async def test_a_run_of_weeks_with_no_surplus_is_not_squashed_into_two_rows() -> None:
    """The mirror of the above, and the one an eighth-of-the-panel chart got wrong.

    A fortnight where the best week merely broke even is still a fortnight of
    deficit, and it earns the rows.
    """
    chart = DivergingBars(height=7)
    async with mounted(chart):
        chart.show([Column("2", 0.0, "0:00"), Column("9", -3.0, "−3:00")])
        drawn = lines(chart)
        rule = next(index for index, row in enumerate(drawn) if BASELINE in row)
        assert rule == 1
        assert len(drawn) == rule + 7


async def test_the_two_arms_are_split_in_proportion_to_the_data() -> None:
    """Not down the middle: a mostly-surplus series gets mostly upward rows."""
    chart = DivergingBars(height=7)
    async with mounted(chart):
        chart.show([Column("2", 5.0, "+5:00"), Column("9", -1.0, "−1:00")])
        drawn = lines(chart)
        rule = next(index for index, row in enumerate(drawn) if BASELINE in row)
        assert rule > len(drawn) - rule - 2


async def test_a_perfectly_balanced_run_draws_the_line_and_nothing_else() -> None:
    """Dividing by an extent of zero is how this used to be a ZeroDivisionError."""
    chart = DivergingBars(height=5)
    async with mounted(chart):
        chart.show([Column("2", 0.0, "0:00"), Column("9", 0.0, "0:00")])
        drawn = lines(chart)
        assert BLOCK not in "".join(drawn[:-1]).replace(BASELINE, "")
        assert BASELINE in "".join(drawn)


async def test_a_year_of_weeks_is_trimmed_from_the_oldest_end() -> None:
    """Fifty-two weeks will not fit a half-width panel, and the old ones go.

    Dropping the newest instead would leave the dashboard reporting on last
    autumn, which is the one thing nobody opens a dashboard to find out.
    """
    chart = DivergingBars()
    async with mounted(chart, width=20):
        width = chart.content_size.width
        chart.show(
            [Column(str(n), float(n), f"+{n}:00") for n in range(width * 3)],
        )
        drawn = lines(chart)
        rule = next(row for row in drawn if BASELINE in row)
        assert len(rule) == width
        assert drawn[-1].startswith(f"best {width * 3 - 1}")


async def test_the_caption_names_the_best_and_the_worst_week() -> None:
    """Direct-labelling all fifty-two would be unreadable, so the extremes carry it."""
    chart = DivergingBars()
    async with mounted(chart):
        chart.show(
            [
                Column("2", 1.0, "+1:00"),
                Column("9", 4.0, "+4:00"),
                Column("16", -3.0, "−3:00"),
            ]
        )
        assert lines(chart)[-1] == "best 9 +4:00 · worst 16 −3:00"


async def test_a_single_week_is_named_once_rather_than_as_both_extremes() -> None:
    """A caption reading `best 2 0:00 · worst 2 0:00` names one week twice."""
    chart = DivergingBars()
    async with mounted(chart):
        chart.show([Column("2", 0.0, "0:00")])
        assert lines(chart)[-1] == "2: 0:00"


async def test_a_caption_for_no_bars_is_blank_rather_than_an_error() -> None:
    """`max` of an empty series raises, and a caption is not worth a traceback."""
    chart = DivergingBars()
    async with mounted(chart):
        assert str(chart._caption(())) == ""


# -- Burndown ----------------------------------------------------------------


async def test_a_burndown_with_no_entitlement_says_so_rather_than_drawing_zero() -> (
    None
):
    chart = Burndown()
    async with mounted(chart):
        assert str(chart.render()) == "No entitlement recorded"
        chart.show(None, 25.0, 12.0)
        assert str(chart.render()) == "No entitlement recorded"
        chart.show(10.0, 0.0, None)
        assert str(chart.render()) == "No entitlement recorded"


async def test_a_burndown_fills_from_the_left_and_prints_its_own_figures() -> None:
    chart = Burndown()
    async with mounted(chart, width=20):
        chart.show(15.0, 25.0, 12.0)
        track, caption = lines(chart)
        assert track.startswith(FULL)
        assert track.endswith(EMPTY)
        assert caption == "10 taken · 15 left · pace 12"


async def test_a_burndown_with_no_pace_draws_no_reference_mark() -> None:
    """Where you should be is not a thing that happened, and is sometimes unknown."""
    chart = Burndown()
    async with mounted(chart, width=20):
        chart.show(15.0, 25.0, None)
        track, caption = lines(chart)
        assert "┃" not in track
        assert caption.endswith("pace 0")


@pytest.mark.parametrize("pace", [-5.0, 40.0])
async def test_the_reference_mark_stays_on_the_track(pace: float) -> None:
    """A pace outside the entitlement is arithmetic, not a reason to index off."""
    chart = Burndown()
    async with mounted(chart, width=20):
        chart.show(15.0, 25.0, pace)
        track = lines(chart)[0]
        assert "┃" in track
        assert len(track) == chart.content_size.width


# -- WeekRibbon --------------------------------------------------------------


async def test_a_ribbon_with_no_days_says_so() -> None:
    ribbon = WeekRibbon(now=NOW)
    async with mounted(ribbon):
        assert str(ribbon.render()) == "Nothing recorded yet"


async def test_each_row_of_the_ribbon_is_named_by_its_day() -> None:
    """A table of weekly totals says how much; the ribbon says when."""
    ribbon = WeekRibbon(now=NOW)
    async with mounted(ribbon, width=40):
        ribbon.show([day(MONDAY), day(MONDAY + timedelta(days=1))], now=NOW)
        drawn = lines(ribbon)
        assert [row[:6] for row in drawn] == ["Mon 02", "Tue 03"]
        assert len({len(row) for row in drawn}) == 1


async def test_a_ribbon_keeps_the_window_it_was_given_until_it_is_given_another() -> (
    None
):
    """Redrawing a week is not a reason to snap back to the default day."""
    ribbon = WeekRibbon(window=Window(time(6), time(20)), now=NOW)
    async with mounted(ribbon):
        ribbon.show([day(MONDAY)], now=NOW)
        assert ribbon.window == Window(time(6), time(20))
        ribbon.show([day(MONDAY)], Window(time(8), time(18)), now=NOW)
        assert ribbon.window == Window(time(8), time(18))


# -- YearHeatmap -------------------------------------------------------------


async def test_a_heatmap_with_no_days_says_so() -> None:
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        assert str(heatmap.render()) == "Nothing recorded yet"


async def test_a_day_nobody_recorded_is_left_blank() -> None:
    """The grid starts on a Monday, so the first week is usually part empty."""
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([day(MONDAY + timedelta(days=2))], first_weekday=0)
        glyph, _ = heatmap._cell(MONDAY)
        assert glyph == " "


@pytest.mark.parametrize(
    "ledger",
    [
        day(MONDAY, working=False),
        day(MONDAY, holiday="Spring bank holiday"),
    ],
    ids=["weekend", "bank holiday"],
)
async def test_a_day_that_was_never_going_to_be_worked_is_drawn_neutral(
    ledger: DayLedger,
) -> None:
    """A Sunday off is not a deficit, and colouring it as one would swamp the year."""
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([ledger], first_weekday=0)
        glyph, style = heatmap._cell(ledger.date)
        assert glyph == EMPTY
        assert style == heatmap.get_component_rich_style("chart--neutral")


async def test_a_day_worked_exactly_to_contract_is_present_but_uncoloured() -> None:
    """It happened, so it is drawn; it was neither good nor bad, so it has no hue."""
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([day(MONDAY)], first_weekday=0)
        glyph, style = heatmap._cell(MONDAY)
        assert glyph == HEAT
        assert style == heatmap.get_component_rich_style("chart--neutral")


async def test_the_ramp_ranks_days_by_how_far_off_they_were() -> None:
    """Four steps an arm is as many as a reader can rank without a legend.

    The colour carries the magnitude and the side carries the sign, so a heavy
    day and a light one are the same hue at different strengths.
    """
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show(
            [
                day(MONDAY, effect=timedelta(hours=4)),
                day(MONDAY + timedelta(days=1), effect=timedelta(hours=1)),
                day(MONDAY + timedelta(days=2), effect=-timedelta(hours=4)),
            ],
            first_weekday=0,
        )
        assert heatmap._cell(MONDAY)[1] == heatmap.get_component_rich_style(
            f"chart--surplus-{DIVERGING_STEPS}"
        )
        assert heatmap._cell(MONDAY + timedelta(days=1))[
            1
        ] == heatmap.get_component_rich_style("chart--surplus-1")
        assert heatmap._cell(MONDAY + timedelta(days=2))[
            1
        ] == heatmap.get_component_rich_style(f"chart--deficit-{DIVERGING_STEPS}")


async def test_a_fortnight_of_near_perfect_days_is_not_drawn_as_a_disaster() -> None:
    """Without a floor the ramp rescales to whatever the worst day happened to be.

    Six minutes over would then be painted as violently as four hours short,
    which turns the one chart meant to show a year at a glance into a liar.
    """
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show(
            [
                day(MONDAY + timedelta(days=n), effect=timedelta(minutes=6))
                for n in (0, 1)
            ],
            first_weekday=0,
        )
        assert heatmap.scale == timedelta(hours=2)
        assert heatmap._cell(MONDAY)[1] == heatmap.get_component_rich_style(
            "chart--surplus-1"
        )


async def test_the_grid_is_a_weekday_per_row_starting_on_monday() -> None:
    """Weekday down, week across: the shape every contribution graph uses.

    It is what puts "my Fridays are short" and "March was heavy" in one picture.
    """
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show(
            [day(MONDAY + timedelta(days=n)) for n in range(14)], first_weekday=0
        )
        drawn = lines(heatmap)
        assert [row[0] for row in drawn[:7]] == list("MTWTFSS")
        assert all(len(row) == 4 for row in drawn[:7])


async def test_the_legend_names_both_ends_of_the_ramp() -> None:
    """Never colour alone. The ramp is drawn with the hours it stands for."""
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([day(MONDAY, effect=timedelta(hours=3))], first_weekday=0)
        legend = lines(heatmap)[-1]
        assert legend.startswith("−3:00 ")
        assert legend.endswith(" +3:00")
        assert legend.count(HEAT) == DIVERGING_STEPS * 2 + 1
        assert AMENDED_HEAT not in legend, "nothing on this year was corrected"


async def test_a_day_written_up_afterwards_carries_its_own_fill() -> None:
    """The ramp says how the day went; the fill says where the reading came from.

    Recoloured instead, the day would drop off the diverging scale it belongs
    on -- the hours are the hours however they were captured.
    """
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([day(MONDAY, segments=(AMENDED,))], first_weekday=0)

        glyph, style = heatmap._cell(MONDAY)
        assert glyph == AMENDED_HEAT
        assert style == heatmap.get_component_rich_style("chart--neutral")
        assert f"{AMENDED_HEAT} corrected" in lines(heatmap)[-1], "and it is named"


async def test_a_punched_day_beside_a_corrected_one_keeps_the_solid_fill() -> None:
    """Both fills on one grid, which is the only way either means anything."""
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show(
            [day(MONDAY, segments=(AMENDED,)), day(MONDAY + timedelta(days=1))],
            first_weekday=0,
        )

        assert heatmap._cell(MONDAY)[0] == AMENDED_HEAT
        assert heatmap._cell(MONDAY + timedelta(days=1))[0] == HEAT


# -- week_columns ------------------------------------------------------------


def test_days_are_grouped_into_weeks_that_begin_on_a_monday() -> None:
    """A week is the unit people plan in.

    A bar a day would be unreadable across a year.
    """
    ledgers = [
        day(MONDAY, effect=timedelta(hours=1)),
        day(MONDAY + timedelta(days=3), effect=timedelta(hours=2)),
        day(MONDAY + timedelta(days=7), effect=-timedelta(hours=1)),
    ]
    assert week_columns(ledgers, first_weekday=0) == [
        Column(label="2", value=3.0, readout="+3:00"),
        Column(label="9", value=-1.0, readout="−1:00"),
    ]


def test_a_week_is_dated_by_its_monday_even_when_it_starts_midweek() -> None:
    """The demo data starts on a Wednesday, and the bar it lands in is that week's."""
    wednesday = MONDAY + timedelta(days=2)
    assert week_columns([day(wednesday)], first_weekday=0)[0].label == "2"


def test_an_absence_still_lands_in_its_week() -> None:
    """A booked day contributes its own effect, so the week is not silently short."""
    booked = day(
        MONDAY,
        absences=(AbsenceSlice(1, AbsenceType.ANNUAL, Portion.FULL),),
        effect=-CONTRACTED,
    )
    assert week_columns([booked], first_weekday=0)[0].value == pytest.approx(-7.4)


# -- an arm is a distance from the baseline, never a value -----------------


@pytest.mark.parametrize(
    ("name", "values"),
    [
        ("one week, behind", [-7.4]),
        ("one week, ahead", [5.0]),
        ("one week, level", [0.0]),
        ("every week behind", [-3.0, -7.4]),
        ("every week ahead", [3.0, 5.0]),
        ("both sides", [-3.0, 5.0]),
    ],
)
async def test_no_shape_of_week_can_take_the_chart_down(
    name: str, values: list[float]
) -> None:
    """The first install has one week on record, and it is usually behind.

    `_high` returned the largest *value* rather than a distance above the
    baseline, so a week of nothing but deficit gave the surplus arm a negative
    height — and `_arms` divides by `high + low`, which for a single column is
    `value + -value`: exactly zero. Opening Insights on a fresh install was a
    ZeroDivisionError before anything was drawn.
    """
    chart = DivergingBars()
    async with mounted(chart):
        chart.show(
            [Column(str(week), value, "") for week, value in enumerate(values, 1)]
        )

        drawn = lines(chart)

        assert any(BASELINE in row for row in drawn), name
        assert len(drawn) == chart.rows + 1, "the arms and the baseline and a caption"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([-3.0], (1, 6)),  # nothing above the line: one row is all it needs
        ([3.0], (6, 1)),  # and the mirror image
        ([-3.0, 5.0], (4, 3)),  # split in proportion to the data
    ],
)
async def test_each_arm_is_given_room_in_proportion_to_its_reach(
    values: list[float], expected: tuple[int, int]
) -> None:
    """A series with no deficit weeks does not need four rows of empty axis."""
    chart = DivergingBars(height=8)
    async with mounted(chart):
        shown = tuple(
            Column(str(week), value, "") for week, value in enumerate(values, 1)
        )

        assert chart._arms(shown) == expected


async def test_the_heatmap_starts_its_rows_on_the_configured_day() -> None:
    """The third chart that assumed Monday, on a screen with two that do not.

    The grid stepped back to `date.weekday() == 0` and labelled its rows with a
    hardcoded "MTWTFSS", while the bars above it take `first_weekday` — so a
    Sunday-first week put the two charts a day out of step with each other and
    with the calendar on the leave screen.
    """
    heatmap = YearHeatmap()
    async with mounted(heatmap, width=40):
        heatmap.show(
            [day(MONDAY + timedelta(days=n)) for n in range(14)], first_weekday=6
        )
        drawn = lines(heatmap)

        assert [row[0] for row in drawn[:7]] == list("SMTWTFS")
