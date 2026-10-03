"""What is left of the palette on a terminal that reports sixteen colours.

`TERM=xterm` with no `COLORTERM`, as `docker run -it`, PuTTY and many SSH
sessions report, and `TERM=linux` on a console leave Rich matching every colour
to the nearest of the sixteen ANSI ones. A colour matched to the same one as the
ground under it is not drawn at all, whatever it reads at full depth.

Measured on what the screens draw, not on a list of the colours they use, so a
rule added next week in a new colour is covered the day it is drawn. Read once
the screen has settled: a frame caught mid-tween blends colours no settled
screen shows.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from rich.color import Color, ColorSystem

from flexi.app import FlexiApp
from flexi.models.database.engine import create_db_engine
from flexi.screens.setup import SetupScreen
from flexi.theme import palette
from tests.database import create_schema
from tests.tui.conftest import WIDE, AppFactory, showing


def standard(colour: Color) -> int | None:
    """The ANSI colour a sixteen-colour terminal is sent instead."""
    return colour.downgrade(ColorSystem.STANDARD).number


def lost_in_sixteen_colours(app: FlexiApp) -> Counter[str]:
    """Glyphs drawn in the colour of their own ground, counted by colour pair.

    Named by palette token where there is one. A reversed glyph is drawn in its
    background colour on its foreground, so the two are swapped back.
    """
    names = {Color.parse(value).triplet: name for name, value in palette().items()}

    def named(colour: Color) -> str:
        triplet = colour.triplet
        return names.get(triplet, triplet.hex if triplet else colour.name)

    lost: Counter[str] = Counter()
    for strip in app.screen._compositor.render_strips():
        for segment in strip:
            style, glyphs = segment.style, segment.text.strip()
            if style is None or not glyphs:
                continue
            ink, ground = style.color, style.bgcolor
            if style.reverse:
                ink, ground = ground, ink
            if ink is None or ground is None:
                continue
            if standard(ink) == standard(ground):
                lost[f"{named(ink)} on {named(ground)}"] += len(glyphs)
    return lost


@pytest.fixture
def unconfigured(tmp_path: Path) -> Path:
    """A migrated database with the setup questions unanswered."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    create_schema(engine)
    engine.dispose()
    return path


@pytest.mark.parametrize("destination", ["dashboard", "leave", "insights", "settings"])
async def test_nothing_on_a_destination_drops_into_its_ground(
    app_factory: AppFactory, destination: str
) -> None:
    """Every rule, track, empty strip and weekend date stays on the screen.

    `c-line` draws them on `c-ink`, and on `c-raised` under a settings field.
    One shade darker it was matched to black, as both grounds are.
    """
    app = app_factory()
    app.animation_level = "none"
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.action_go_to(destination)
        await pilot.pause()

        assert lost_in_sixteen_colours(app) == Counter()


async def test_nothing_on_the_setup_form_drops_into_its_ground(
    unconfigured: Path,
) -> None:
    """The first screen anyone sees, with its notes and rail in the hairline."""
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        # The form fades in, so it is not drawn at all until that has run.
        await pilot.wait_for_scheduled_animations()
        await pilot.pause()
        showing(app, SetupScreen)

        assert lost_in_sixteen_colours(app) == Counter()
