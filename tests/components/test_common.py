"""The shared widgets, one at a time.

Each of these is a wrapper whose whole job is to carry a class or to lay a
figure out, so the full application is the wrong instrument for them: a test
here mounts the single widget it is about into an otherwise empty app, which
costs a hundredth of a second where driving the dashboard costs a second and a
half. The stylesheets are the real ones, because a component class that no rule
matches is the failure most of these tests exist to catch.
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
async def mounted(*widgets: Widget) -> AsyncIterator[Pilot[None]]:
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

    async with Harness().run_test(size=(60, 20)) as pilot:
        yield pilot


def colours(text: Text) -> list[str]:
    """The colour of every character, as the terminal would paint it."""
    painted: list[str] = []
    for segment in text.render(CONSOLE):
        style = segment.style
        name = style.color.name if style is not None and style.color else ""
        painted.extend([name] * len(segment.text))
    return painted


# -- the width classes -------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "narrow", "tiny"),
    [
        (NARROW_COLUMNS, False, False),
        (NARROW_COLUMNS - 1, True, False),
        (TINY_COLUMNS, True, False),
        (TINY_COLUMNS - 1, True, True),
    ],
)
def test_the_fold_classes_follow_the_terminal(
    *, width: int, narrow: bool, tiny: bool
) -> None:
    """Terminal CSS has no media query, so the class is the query.

    The thresholds are inclusive at the top: a terminal exactly as wide as the
    two-column layout asks for gets the two-column layout.
    """
    node = Static()
    mark_width(node, width)
    assert node.has_class("-narrow") is narrow
    assert node.has_class("-tiny") is tiny


def test_widening_the_terminal_takes_the_fold_classes_back_off() -> None:
    """A screen calls this on every resize, including the ones that grow.

    Marking on the way down and never unmarking would leave a maximised window
    drawn in the one-column layout it was dragged out of.
    """
    node = Static()
    mark_width(node, TINY_COLUMNS - 1)
    mark_width(node, NARROW_COLUMNS + 20)
    assert not node.has_class("-narrow")
    assert not node.has_class("-tiny")


# -- Pill --------------------------------------------------------------------


async def test_a_pill_with_nothing_to_say_is_not_drawn() -> None:
    """`.pill` carries a ground and a min-width, so an empty one is a block."""
    pill = Pill()
    async with mounted(pill):
        assert pill.display is False
        pill.set_state("on the clock", Tone.OK)
        assert pill.display is True


async def test_a_pill_of_whitespace_is_still_nothing_to_say() -> None:
    pill = Pill("   ")
    async with mounted(pill):
        assert pill.display is False


async def test_a_pill_shows_the_label_it_was_given() -> None:
    pill = Pill("3 left", Tone.WARN)
    async with mounted(pill):
        assert str(pill.render()) == "3 left"
        assert pill.has_class("pill--warn")


async def test_a_pill_wears_one_tone_at_a_time() -> None:
    """They are mutually exclusive, and two grounds at once is a colour bug.

    Cheaper to clear all of them than to track which one is on, and this is the
    test that says the clearing has to keep happening.
    """
    pill = Pill("late", Tone.ERR)
    async with mounted(pill):
        assert pill.has_class("pill--err")
        pill.set_state("done", Tone.OK)
        assert pill.has_class("pill--ok")
        assert not pill.has_class("pill--err")


async def test_a_neutral_pill_carries_no_tone_class_at_all() -> None:
    """Neutral is the absence of a tone rather than a fifth colour."""
    pill = Pill("no data", Tone.ACCENT)
    async with mounted(pill):
        pill.set_state("no data", Tone.NEUTRAL)
        assert not any(name.startswith("pill--") for name in pill.classes)


# -- StatCard ----------------------------------------------------------------


def card_lines(card: StatCard) -> list[str]:
    return [str(child.render()) for child in card.query(Static)]


async def test_a_stat_card_draws_its_label_value_and_note() -> None:
    card = StatCard("Balance", "+3:20", "since 1 April")
    async with mounted(card):
        assert card_lines(card) == ["Balance", "+3:20", "since 1 April"]


async def test_a_stat_card_redraws_only_the_line_that_changed() -> None:
    card = StatCard("Balance", "+3:20", "since 1 April")
    async with mounted(card):
        card.value = "+4:00"
        card.note = "since 6 April"
        assert card_lines(card) == ["Balance", "+4:00", "since 6 April"]


async def test_a_value_set_before_the_card_is_mounted_is_still_drawn() -> None:
    """Reactives fire before the first compose.

    A watcher that queried its children then would raise rather than return
    nothing, so it stands down until there is something to query -- and the
    value has to survive that, or a card filled in during ``on_mount`` of the
    screen above it would come up blank.
    """
    card = StatCard("Balance")
    card.value = "+3:20"
    card.note = "six weeks"
    async with mounted(card):
        assert card_lines(card) == ["Balance", "+3:20", "six weeks"]


# -- the small wrappers ------------------------------------------------------


async def test_a_key_hint_names_the_key_and_what_it_does() -> None:
    hint = KeyHint("space", "expand")
    async with mounted(hint):
        keys = hint.query(".kbd")
        actions = hint.query(".key-hint-action")
        assert str(next(iter(keys)).render()) == "space"
        assert str(next(iter(actions)).render()) == "expand"


def test_a_rule_is_accented_only_when_it_is_asked_to_be() -> None:
    assert Rule("This week", accent=True).has_class("rule--accent")
    assert not Rule("This week").has_class("rule--accent")


def test_an_empty_region_says_so_in_words() -> None:
    """Hatching alone reads as a widget that failed to render."""
    assert str(EmptyIndicator().render()) == "Nothing here yet"
    assert str(EmptyIndicator("No leave booked").render()) == "No leave booked"


# -- Gauge -------------------------------------------------------------------


async def test_an_unmeasured_allowance_reads_as_a_dash_rather_than_zero() -> None:
    """An allowance nobody has recorded and one recorded at zero differ.

    Drawing the first as a full-looking track at zero would tell somebody with
    no entitlement configured that they had used everything.
    """
    gauge = Gauge("Annual")
    async with mounted(gauge):
        gauge.show(None, tone=Tone.OK, total=25.0)
        assert "—" in str(gauge._headline(20))
        painted = set(colours(gauge._bar(20)))
        fill = gauge.get_component_rich_style("gauge--good").color
        assert fill is not None
        assert fill.name not in painted


async def test_a_reading_fills_the_track_from_the_left() -> None:
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


async def test_a_gauge_with_nothing_to_measure_against_draws_an_empty_track() -> None:
    """Zero is a real total to arrive at: nobody has been given any leave."""
    gauge = Gauge("Annual")
    async with mounted(gauge):
        gauge.show(5.0, target=2.0, tone=Tone.OK, total=0.0)
        bar = gauge._bar(20)
        assert MARKER not in str(bar)
        assert len(set(colours(bar))) == 1


async def test_the_pace_marker_is_drawn_where_you_should_be() -> None:
    """18.5 days left is comfortable or alarming depending on the date."""
    gauge = Gauge("Annual")
    async with mounted(gauge):
        gauge.show(10.0, target=10.0, tone=Tone.OK, total=20.0)
        bar = gauge._bar(21)
        assert str(bar).index(MARKER) == 10
        target = gauge.get_component_rich_style("gauge--target").color
        assert target is not None
        assert colours(bar)[10] == target.name


async def test_a_gauge_has_no_total_until_it_is_given_a_reading() -> None:
    """One place for the total, and one meaning for `None` in `show`.

    It was a constructor argument as well, which no production caller passed,
    so `show` carried a sentinel to decide which of the two won -- a third
    meaning for `None` in a signature where it already meant "no reading" and
    "no marker".
    """
    gauge = Gauge("Annual")
    async with mounted(gauge):
        assert gauge.total == 0.0
        gauge.show(10.0, total=25.0)
        assert gauge.total == 25.0
        gauge.show(10.0, total=30.0)
        assert gauge.total == 30.0


async def test_the_label_gives_way_before_the_figure_does() -> None:
    """A wrapped headline costs the row the bar was going to be drawn in.

    So a narrow wallet loses its words rather than its gauges, and the figure --
    the only part that cannot be guessed from context -- is the last to go.
    """
    gauge = Gauge("Annual leave remaining")
    async with mounted(gauge):
        gauge.show(18.5, readout="18.5 days", total=25.0)
        headline = str(gauge._headline(14))
        assert headline.endswith("18.5 days")
        assert len(headline) == 14

        gauge.show(18.5, readout="18.5 days remaining of 25", total=25.0)
        assert str(gauge._headline(12)).lstrip() == "18.5 days remaining of 25"


async def test_a_compact_gauge_keeps_the_line_and_drops_the_bar() -> None:
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


async def test_a_gauge_reads_its_own_value_out_when_it_is_given_no_words() -> None:
    gauge = Gauge("Days")
    async with mounted(gauge):
        gauge.show(2.5, total=10.0)
        assert str(gauge._headline(20)).endswith("2.5")
