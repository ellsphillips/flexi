"""The shared widgets, one at a time, in an otherwise empty app.

The stylesheets are the real ones: a component class no rule matches is the
failure most of these tests exist to catch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import PurePath
from typing import ClassVar

import pytest
from rich.console import Console
from rich.text import Text
from textual.app import App, ComposeResult
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import Static

from flexi.components.common import (
    MARKER,
    NARROW_COLUMNS,
    TINY_COLUMNS,
    EmptyIndicator,
    Gauge,
    KeyHint,
    Pill,
    Rule,
    StatCard,
    Tone,
    mark_width,
)
from flexi.theme import THEME_NAME, THEME_PATH, flexi_theme

PACKAGE = THEME_PATH.parent.parent
CONSOLE = Console()


@asynccontextmanager
async def mounted(
    *widgets: Widget, size: tuple[int, int] = (60, 20)
) -> AsyncIterator[Pilot[None]]:
    """Run the given widgets in an app that has the palette and nothing else."""

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
            yield from widgets

    async with Harness().run_test(size=size) as pilot:
        yield pilot


def colours(text: Text) -> list[str]:
    """The colour of every character, as the terminal would paint it."""
    painted: list[str] = []
    for segment in text.render(CONSOLE):
        style = segment.style
        name = style.color.name if style is not None and style.color else ""
        painted.extend([name] * len(segment.text))
    return painted


# ---- the width classes ----


@pytest.mark.parametrize(
    ("width", "narrow", "tiny"),
    [
        (NARROW_COLUMNS, False, False),
        (NARROW_COLUMNS - 1, True, False),
        (TINY_COLUMNS, True, False),
        (TINY_COLUMNS - 1, True, True),
    ],
)
def test_fold_classes_follow_the_width(*, width: int, narrow: bool, tiny: bool) -> None:
    """Terminal CSS has no media query, so the class is the query.

    The thresholds are inclusive at the top.
    """
    node = Static()
    mark_width(node, width)
    assert node.has_class("-narrow") is narrow
    assert node.has_class("-tiny") is tiny


def test_widening_removes_the_fold_classes() -> None:
    """A screen calls this on every resize, including the ones that grow."""
    node = Static()
    mark_width(node, TINY_COLUMNS - 1)
    mark_width(node, NARROW_COLUMNS + 20)
    assert not node.has_class("-narrow")
    assert not node.has_class("-tiny")


# ---- Pill ----


async def test_empty_pill_is_not_drawn() -> None:
    """`.pill` carries a ground and a min-width, so an empty one is a block."""
    pill = Pill()
    async with mounted(pill):
        assert pill.display is False
        pill.set_state("on the clock", Tone.OK)
        assert pill.display is True


async def test_whitespace_pill_is_not_drawn() -> None:
    pill = Pill("   ")
    async with mounted(pill):
        assert pill.display is False


async def test_pill_shows_its_label() -> None:
    pill = Pill("3 left", Tone.WARN)
    async with mounted(pill):
        assert str(pill.render()) == "3 left"
        assert pill.has_class("pill--warn")


async def test_pill_wears_one_tone_at_a_time() -> None:
    """Tone classes are mutually exclusive; two grounds at once is a colour bug."""
    pill = Pill("late", Tone.ERR)
    async with mounted(pill):
        assert pill.has_class("pill--err")
        pill.set_state("done", Tone.OK)
        assert pill.has_class("pill--ok")
        assert not pill.has_class("pill--err")


async def test_neutral_pill_carries_no_tone_class() -> None:
    """Neutral is the absence of a tone, not a fifth colour."""
    pill = Pill("no data", Tone.ACCENT)
    async with mounted(pill):
        pill.set_state("no data", Tone.NEUTRAL)
        assert not any(name.startswith("pill--") for name in pill.classes)


# ---- StatCard ----


def card_lines(card: StatCard) -> list[str]:
    return [str(child.render()) for child in card.query(Static)]


async def test_stat_card_draws_label_value_note() -> None:
    card = StatCard("Balance", "+3:20", "since 1 April")
    async with mounted(card):
        assert card_lines(card) == ["Balance", "+3:20", "since 1 April"]


async def test_stat_card_redraws_only_what_changed() -> None:
    card = StatCard("Balance", "+3:20", "since 1 April")
    async with mounted(card):
        card.value = "+4:00"
        card.note = "since 6 April"
        assert card_lines(card) == ["Balance", "+4:00", "since 6 April"]


async def test_value_set_before_mount_is_drawn() -> None:
    """Reactives fire before the first compose, so the watcher stands down."""
    card = StatCard("Balance")
    card.value = "+3:20"
    card.note = "six weeks"
    async with mounted(card):
        assert card_lines(card) == ["Balance", "+3:20", "six weeks"]


# ---- the small wrappers ----


async def test_key_hint_names_key_and_action() -> None:
    hint = KeyHint("space", "expand")
    async with mounted(hint):
        keys = hint.query(".kbd")
        actions = hint.query(".key-hint-action")
        assert str(next(iter(keys)).render()) == "space"
        assert str(next(iter(actions)).render()) == "expand"


def test_rule_is_accented_only_on_request() -> None:
    assert Rule("This week", accent=True).has_class("rule--accent")
    assert not Rule("This week").has_class("rule--accent")


def test_empty_region_says_so_in_words() -> None:
    """Hatching alone reads as a widget that failed to render."""
    assert str(EmptyIndicator().render()) == "Nothing here yet"
    assert str(EmptyIndicator("No leave booked").render()) == "No leave booked"


# ---- Gauge ----


async def test_unmeasured_allowance_reads_as_a_dash() -> None:
    """An unrecorded allowance and one recorded at zero differ."""
    gauge = Gauge("Annual")
    async with mounted(gauge):
        gauge.show(None, tone=Tone.OK, total=25.0)
        assert "—" in str(gauge._headline(20))
        painted = set(colours(gauge._bar(20)))
        fill = gauge.get_component_rich_style("gauge--good").color
        assert fill is not None
        assert fill.name not in painted


async def test_reading_fills_the_track_from_left() -> None:
    gauge = Gauge("Annual")
    async with mounted(gauge):
        gauge.show(25.0, tone=Tone.OK, total=25.0)
        fill = gauge.get_component_rich_style("gauge--good").color
        assert fill is not None
        assert set(colours(gauge._bar(20))) == {fill.name}

        gauge.show(5.0, tone=Tone.OK, total=25.0)
        painted = colours(gauge._bar(20))
        assert painted[0] == fill.name
        assert painted[-1] != fill.name


async def test_zero_total_draws_an_empty_track() -> None:
    """Zero is a real total to arrive at: no leave has been granted."""
    gauge = Gauge("Annual")
    async with mounted(gauge):
        gauge.show(5.0, target=2.0, tone=Tone.OK, total=0.0)
        bar = gauge._bar(20)
        assert MARKER not in str(bar)
        assert len(set(colours(bar))) == 1


async def test_pace_marker_is_drawn_at_the_target() -> None:
    """18.5 days left is comfortable or alarming depending on the date."""
    gauge = Gauge("Annual")
    async with mounted(gauge):
        gauge.show(10.0, target=10.0, tone=Tone.OK, total=20.0)
        bar = gauge._bar(21)
        assert str(bar).index(MARKER) == 10
        target = gauge.get_component_rich_style("gauge--target").color
        assert target is not None
        assert colours(bar)[10] == target.name


async def test_gauge_total_comes_from_show() -> None:
    """One place for the total, and one meaning for `None` in `show`."""
    gauge = Gauge("Annual")
    async with mounted(gauge):
        assert gauge.total == 0.0
        gauge.show(10.0, total=25.0)
        assert gauge.total == 25.0
        gauge.show(10.0, total=30.0)
        assert gauge.total == 30.0


async def test_label_gives_way_before_the_figure() -> None:
    """A wrapped headline costs the row the bar was going to be drawn in."""
    gauge = Gauge("Annual leave remaining")
    async with mounted(gauge):
        gauge.show(18.5, readout="18.5 days", total=25.0)
        headline = str(gauge._headline(14))
        assert headline.endswith("18.5 days")
        assert len(headline) == 14

        gauge.show(18.5, readout="18.5 days remaining of 25", total=25.0)
        assert str(gauge._headline(12)).lstrip() == "18.5 days remaining of 25"


async def test_compact_gauge_drops_the_bar() -> None:
    """An empty track is a row of hyphens costing a line of a crowded sidebar."""
    gauge = Gauge("TOIL")
    async with mounted(gauge) as pilot:
        gauge.show(2.0, readout="2 days", compact=True, total=5.0)
        await pilot.pause()
        assert "\n" not in str(gauge.render())
        assert gauge.styles.height is not None
        assert gauge.styles.height.value == 1

        gauge.show(2.0, readout="2 days", total=5.0)
        await pilot.pause()
        assert "\n" in str(gauge.render())
        assert gauge.styles.height is not None
        assert gauge.styles.height.value == 2


async def test_gauge_reads_its_value_without_words() -> None:
    gauge = Gauge("Days")
    async with mounted(gauge):
        gauge.show(2.5, total=10.0)
        assert str(gauge._headline(20)).endswith("2.5")
