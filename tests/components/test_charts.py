"""The character charts, checked as pictures and as captions.

Each widget is mounted alone into an app carrying the real stylesheets: the
ramp is only a ramp if the CSS behind it defines eight distinct colours, and a
bare app draws every step in the same white.
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
"""When these ribbons are drawn: the end of the day, so a closed day redraws
identically."""

AMENDED = Segment(
    session_id=1,
    start=wallclock.local(datetime.combine(MONDAY, time(9))),
    end=wallclock.local(datetime.combine(MONDAY, time(17))),
    amended=True,
)
"""A morning written up after the fact, with no punch behind it."""


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
    """A ledger whose balance effect is what the test asked for."""
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


# ---- DivergingBars ----


async def test_chart_with_no_weeks_says_so() -> None:
    chart = DivergingBars()
    async with mounted(chart):
        assert str(chart.render()) == "Nothing recorded yet"


async def test_surplus_above_the_line_deficit_below() -> None:
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


async def test_weeks_with_no_deficit_keep_one_row() -> None:
    chart = DivergingBars(height=7)
    async with mounted(chart):
        chart.show([Column("2", 3.0, "+3:00"), Column("9", 1.0, "+1:00")])
        drawn = lines(chart)
        rule = next(index for index, row in enumerate(drawn) if BASELINE in row)
        assert rule == 5
        assert len(drawn) == rule + 3


async def test_weeks_with_no_surplus_keep_their_rows() -> None:
    chart = DivergingBars(height=7)
    async with mounted(chart):
        chart.show([Column("2", 0.0, "0:00"), Column("9", -3.0, "−3:00")])
        drawn = lines(chart)
        rule = next(index for index, row in enumerate(drawn) if BASELINE in row)
        assert rule == 1
        assert len(drawn) == rule + 7


async def test_arms_split_in_proportion_to_the_data() -> None:
    chart = DivergingBars(height=7)
    async with mounted(chart):
        chart.show([Column("2", 5.0, "+5:00"), Column("9", -1.0, "−1:00")])
        drawn = lines(chart)
        rule = next(index for index, row in enumerate(drawn) if BASELINE in row)
        assert rule > len(drawn) - rule - 2


async def test_balanced_run_draws_only_the_line() -> None:
    """Every column at zero gives the two arms an extent of zero to divide by."""
    chart = DivergingBars(height=5)
    async with mounted(chart):
        chart.show([Column("2", 0.0, "0:00"), Column("9", 0.0, "0:00")])
        drawn = lines(chart)
        assert BLOCK not in "".join(drawn[:-1]).replace(BASELINE, "")
        assert BASELINE in "".join(drawn)


async def test_weeks_are_trimmed_from_the_oldest_end() -> None:
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


async def test_caption_names_the_best_and_worst_week() -> None:
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


async def test_single_week_is_named_once() -> None:
    """A caption reading `best 2 0:00 · worst 2 0:00` names one week twice."""
    chart = DivergingBars()
    async with mounted(chart):
        chart.show([Column("2", 0.0, "0:00")])
        assert lines(chart)[-1] == "2: 0:00"


async def test_caption_for_no_bars_is_blank() -> None:
    """`max` of an empty series raises, and a caption is not worth a traceback."""
    chart = DivergingBars()
    async with mounted(chart):
        assert str(chart._caption(())) == ""


# ---- Burndown ----


async def test_burndown_with_no_entitlement_says_so() -> None:
    chart = Burndown()
    async with mounted(chart):
        assert str(chart.render()) == "No entitlement recorded"
        chart.show(None, 25.0, 12.0)
        assert str(chart.render()) == "No entitlement recorded"
        chart.show(10.0, 0.0, None)
        assert str(chart.render()) == "No entitlement recorded"


async def test_burndown_fills_left_and_prints_its_figures() -> None:
    chart = Burndown()
    async with mounted(chart, width=20):
        chart.show(15.0, 25.0, 12.0)
        track, caption = lines(chart)
        assert track.startswith(FULL)
        assert track.endswith(EMPTY)
        assert caption == "10 taken · 15 left · pace 12"


async def test_burndown_signs_an_overspent_remainder() -> None:
    """Every negative in the panel is written with U+2212, not an ASCII hyphen."""
    chart = Burndown()
    async with mounted(chart, width=20):
        chart.show(-2.0, 25.0, 12.0)
        assert lines(chart)[1] == "27 taken · −2 left · pace 12"


async def test_burndown_with_no_pace_has_no_mark() -> None:
    chart = Burndown()
    async with mounted(chart, width=20):
        chart.show(15.0, 25.0, None)
        track, caption = lines(chart)
        assert "┃" not in track
        assert caption.endswith("pace 0")


@pytest.mark.parametrize("pace", [-5.0, 40.0])
async def test_reference_mark_stays_on_the_track(pace: float) -> None:
    """A pace outside the entitlement is arithmetic, not an index off the end."""
    chart = Burndown()
    async with mounted(chart, width=20):
        chart.show(15.0, 25.0, pace)
        track = lines(chart)[0]
        assert "┃" in track
        assert len(track) == chart.content_size.width


# ---- WeekRibbon ----


async def test_ribbon_with_no_days_says_so() -> None:
    ribbon = WeekRibbon(now=NOW)
    async with mounted(ribbon):
        assert str(ribbon.render()) == "Nothing recorded yet"


async def test_each_ribbon_row_is_named_by_its_day() -> None:
    ribbon = WeekRibbon(now=NOW)
    async with mounted(ribbon, width=40):
        ribbon.show([day(MONDAY), day(MONDAY + timedelta(days=1))], now=NOW)
        drawn = lines(ribbon)
        assert [row[:6] for row in drawn] == ["Mon 02", "Tue 03"]
        assert len({len(row) for row in drawn}) == 1


async def test_ribbon_keeps_its_window_until_given_another() -> None:
    ribbon = WeekRibbon(window=Window(time(6), time(20)), now=NOW)
    async with mounted(ribbon):
        ribbon.show([day(MONDAY)], now=NOW)
        assert ribbon.window == Window(time(6), time(20))
        ribbon.show([day(MONDAY)], Window(time(8), time(18)), now=NOW)
        assert ribbon.window == Window(time(8), time(18))


# ---- YearHeatmap ----


async def test_heatmap_with_no_days_says_so() -> None:
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        assert str(heatmap.render()) == "Nothing recorded yet"


async def test_unrecorded_day_is_left_blank() -> None:
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
async def test_day_never_due_to_be_worked_is_neutral(
    ledger: DayLedger,
) -> None:
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([ledger], first_weekday=0)
        glyph, style = heatmap._cell(ledger.date)
        assert glyph == EMPTY
        assert style == heatmap.get_component_rich_style("chart--neutral")


async def test_day_worked_to_contract_is_uncoloured() -> None:
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([day(MONDAY)], first_weekday=0)
        glyph, style = heatmap._cell(MONDAY)
        assert glyph == HEAT
        assert style == heatmap.get_component_rich_style("chart--neutral")


async def test_ramp_ranks_days_by_how_far_off() -> None:
    """Colour carries the magnitude and the side of the line carries the sign."""
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


async def test_near_perfect_days_stay_pale() -> None:
    """Without a floor the ramp rescales to the worst day in the series."""
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


async def test_grid_is_a_weekday_per_row_from_monday() -> None:
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show(
            [day(MONDAY + timedelta(days=n)) for n in range(14)], first_weekday=0
        )
        drawn = lines(heatmap)
        assert [row[0] for row in drawn[:7]] == list("MTWTFSS")
        assert all(len(row) == 4 for row in drawn[:7])


async def test_legend_names_both_ends_of_the_ramp() -> None:
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([day(MONDAY, effect=timedelta(hours=3))], first_weekday=0)
        legend = lines(heatmap)[-1]
        assert legend.startswith("−3:00 ")
        assert legend.endswith(" +3:00")
        assert legend.count(HEAT) == DIVERGING_STEPS * 2 + 1
        assert AMENDED_HEAT not in legend, "nothing on this year was corrected"


async def test_day_written_up_afterwards_has_its_own_fill() -> None:
    """The ramp says how the day went; the fill says where the reading came from."""
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show([day(MONDAY, segments=(AMENDED,))], first_weekday=0)

        glyph, style = heatmap._cell(MONDAY)
        assert glyph == AMENDED_HEAT
        assert style == heatmap.get_component_rich_style("chart--neutral")
        assert f"{AMENDED_HEAT} corrected" in lines(heatmap)[-1], "and it is named"


async def test_punched_and_corrected_days_keep_their_fills() -> None:
    heatmap = YearHeatmap()
    async with mounted(heatmap):
        heatmap.show(
            [day(MONDAY, segments=(AMENDED,)), day(MONDAY + timedelta(days=1))],
            first_weekday=0,
        )

        assert heatmap._cell(MONDAY)[0] == AMENDED_HEAT
        assert heatmap._cell(MONDAY + timedelta(days=1))[0] == HEAT


# ---- week_columns ----


def test_days_are_grouped_into_weeks_beginning_on_monday() -> None:
    ledgers = [
        day(MONDAY, effect=timedelta(hours=1)),
        day(MONDAY + timedelta(days=3), effect=timedelta(hours=2)),
        day(MONDAY + timedelta(days=7), effect=-timedelta(hours=1)),
    ]
    assert week_columns(ledgers, first_weekday=0) == [
        Column(label="2", value=3.0, readout="+3:00"),
        Column(label="9", value=-1.0, readout="−1:00"),
    ]


def test_midweek_start_is_dated_by_its_monday() -> None:
    """The demo data starts on a Wednesday and lands in that week's bar."""
    wednesday = MONDAY + timedelta(days=2)
    assert week_columns([day(wednesday)], first_weekday=0)[0].label == "2"


def test_absence_still_lands_in_its_week() -> None:
    """A booked day contributes its own effect, so the week is not short."""
    booked = day(
        MONDAY,
        absences=(AbsenceSlice(1, AbsenceType.ANNUAL, Portion.FULL),),
        effect=-CONTRACTED,
    )
    assert week_columns([booked], first_weekday=0)[0].value == pytest.approx(-7.4)


# ---- an arm is a distance from the baseline, never a value ----


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
    """`_arms` divides by `high + low`, and both are distances, never values."""
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
    chart = DivergingBars(height=8)
    async with mounted(chart):
        shown = tuple(
            Column(str(week), value, "") for week, value in enumerate(values, 1)
        )

        assert chart._arms(shown) == expected


async def test_heatmap_rows_start_on_the_configured_day() -> None:
    """The row labels and the step-back follow `first_weekday`, as the bars do."""
    heatmap = YearHeatmap()
    async with mounted(heatmap, width=40):
        heatmap.show(
            [day(MONDAY + timedelta(days=n)) for n in range(14)], first_weekday=6
        )
        drawn = lines(heatmap)

        assert [row[0] for row in drawn[:7]] == list("SMTWTFS")
