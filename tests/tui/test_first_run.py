"""Install, launch, answer five questions, and get to the dashboard.

This is the only path every single user takes, and the one nobody runs again
after their first day -- so it is the one most likely to rot unnoticed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Input, Select

from flexi.app import FlexiApp
from flexi.components.wordmark import Wordmark
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.setup import GUTTER, Question, Rail, SetupScreen, form_rows
from flexi.services.settings import SettingsService
from flexi.theme import MARK_LIVE, TAIL, colour
from tests.tui.conftest import WIDE, showing


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """A migrated database with nothing in it, as run_migrations would leave it."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


async def _answer(app: FlexiApp, working_days: str) -> None:
    screen = showing(app, SetupScreen)
    screen.query_one("#input-leave-start", Input).value = "04-06"
    screen.query_one("#input-entitlement", Input).value = "28"
    screen.query_one("#input-working-days", Input).value = working_days
    screen.query_one("#select-division", Select).value = "scotland"
    screen.query_one("#input-auto-close", Input).value = "18:30"


async def test_a_fresh_database_opens_on_setup(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)


@pytest.mark.parametrize("working_days", ["Mon-Fri", "0,1,2,3,4", "mon, tue, wed"])
async def test_setup_accepts_a_reasonable_answer_and_lands_on_the_dashboard(
    fresh_db: Path, working_days: str
) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, working_days)
        await pilot.pause()
        showing(app, SetupScreen).action_save()
        await pilot.pause()
        await pilot.pause()
        showing(app, DashboardScreen)


async def test_a_second_launch_goes_straight_to_the_dashboard(fresh_db: Path) -> None:
    """The regression: setup used to succeed and the next launch to raise."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        await pilot.pause()
        showing(app, SetupScreen).action_save()
        await pilot.pause()
        await pilot.pause()

    again = FlexiApp(db_path=fresh_db)
    async with again.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(again, DashboardScreen)


async def test_setup_refuses_an_answer_it_cannot_read(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "whenever")
        await pilot.pause()
        showing(app, SetupScreen).action_save()
        await pilot.pause()

        showing(app, SetupScreen)  # still here, not dismissed

    with get_session(create_db_engine(fresh_db)) as session:
        assert SettingsService(session).get_settings() is None


async def test_what_was_answered_is_what_was_saved(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Tue-Thu")
        await pilot.pause()
        showing(app, SetupScreen).action_save()
        await pilot.pause()
        await pilot.pause()

    with get_session(create_db_engine(fresh_db)) as session:
        settings = SettingsService(session)
        stored = settings.get_settings()
        assert stored is not None
        assert stored.leave_year_start == "04-06"
        assert stored.bank_holiday_division == "scotland"
        assert stored.auto_close_time == "18:30"
        assert settings.get_working_day_indices() == [1, 2, 3]
        assert settings.get_active_entitlement_days(None) == 28.0


async def test_the_wordmark_lands_and_the_questions_arrive_under_it(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of `flexi init` on a new machine, animation and all.

    The animation used to be a screen of its own pushed over this one, and
    `Screen.dismiss` pops the top of the stack rather than the screen it is
    called on -- so when the word landed it deleted the form and left its own
    last frame with nothing behind it. It is a widget on this screen now, so
    there is no second screen to pop and the questions arrive underneath it.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)

    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        questions = screen.query_one("#setup-questions")
        assert not questions.has_class("-arrived"), "they wait for the word to stop"

        screen.query_one(Wordmark).skip()
        await pilot.pause()
        assert questions.has_class("-arrived"), "and arrive when it has"

        await _answer(app, "Mon-Fri")
        await pilot.pause()
        screen.action_save()
        await pilot.pause()
        await pilot.pause()
        showing(app, DashboardScreen)

    with get_session(create_db_engine(fresh_db)) as session:
        stored = SettingsService(session).get_settings()
        assert stored is not None, "the answers survived the animation"
        assert stored.leave_year_start == "04-06"


def _logo_span(app: FlexiApp) -> tuple[int, int]:
    """The first and last column the drawn wordmark occupies."""
    rows = [
        "".join(segment.text for segment in strip).rstrip()
        for strip in app.screen._compositor.render_strips()
    ]
    ink = [row for row in rows if "█" in row]
    assert ink, "the wordmark should be on screen"
    return min(len(row) - len(row.lstrip()) for row in ink), max(
        len(row) for row in ink
    )


@pytest.mark.parametrize("width", [92, 104, 120])
async def test_the_wordmark_is_centred_over_the_questions(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    """It was as wide as its canvas, and the questions are wider than that.

    A narrower widget sits against the left edge of the column it is in, which
    is correctly centred as a block and visibly off to one side as a logo.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(width, 34)) as pilot:
        await pilot.pause()
        showing(app, SetupScreen).query_one(Wordmark).skip()
        for _ in range(24):
            await pilot.pause()

        left, right = _logo_span(app)
        questions = app.screen.query_one("#setup-questions").region
        assert abs((left + right) / 2 - (questions.x + questions.width / 2)) <= 1


async def test_the_wordmark_does_not_move_sideways_when_the_questions_arrive(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reveal widens the column, and the logo used to slide left with it."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 34)) as pilot:
        await pilot.pause()
        wordmark = showing(app, SetupScreen).query_one(Wordmark)
        before = wordmark.region.x

        wordmark.skip()
        for _ in range(24):
            await pilot.pause()

        assert wordmark.region.x == before


async def test_the_wordmark_rises_to_make_room(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The questions open out and push the logo up, rather than replacing it."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 34)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        wordmark = screen.query_one(Wordmark)
        questions = screen.query_one("#setup-questions")
        assert questions.region.height == 0, "closed until the word has stopped"
        settled = wordmark.region.y

        wordmark.skip()
        for _ in range(24):
            await pilot.pause()

        assert wordmark.region.y < settled, "the logo should have moved up"
        assert questions.region.height > 0


async def test_the_counted_height_is_the_real_one(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rise animates to a counted height, because measuring means a flash.

    Counting is only safe while it agrees with what the stylesheet lays out, so
    this is the thing that stops the two drifting apart in silence.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        for _ in range(24):
            await pilot.pause()

        counted = form_rows(len(screen.query(Question)))
        assert screen.query_one("#setup-questions").region.height == counted


async def test_the_questions_open_out_rather_than_appear(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless, animations resolve at once, so the rise cannot be watched.

    `test_the_wordmark_rises_to_make_room` checks where everything ends up, and
    passes just as happily if the height is assigned instead of animated. What
    can still be checked is that the rise is asked for -- and that is the whole
    difference between a logo making room and a screen jumping.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    asked: list[str] = []

    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 34)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        questions = screen.query_one("#setup-questions")
        animate = questions.styles.animate

        def spy(attribute: str, *args: Any, **kwargs: Any) -> Any:
            asked.append(attribute)
            return animate(attribute, *args, **kwargs)

        monkeypatch.setattr(questions.styles, "animate", spy)
        screen.query_one(Wordmark).skip()
        await pilot.pause()

    assert "height" in asked, "the questions should open out, not switch on"
    assert "opacity" in asked, "and fade up as they do"


async def test_the_rail_is_one_unbroken_line(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rail drawn a piece per question has a gap wherever the rows are spaced.

    That is a dotted line pretending to be a continuous one, and a marker made
    of pieces can only blink from one to the next.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        for _ in range(24):
            await pilot.pause()

        rail = screen.query_one(Rail)
        rows = [
            "".join(segment.text for segment in strip)
            for strip in app.screen._compositor.render_strips()
        ]
        column = rail.region.x + len(GUTTER)
        drawn = "".join(
            rows[rail.region.y + step][column] for step in range(rail.region.height)
        )

        assert rail.region.height == form_rows(len(screen.query(Question)))
        assert " " not in drawn, f"the rail has a gap in it: {drawn!r}"


async def test_the_marker_sits_on_the_question_holding_the_cursor(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        for _ in range(24):
            await pilot.pause()

        rail = screen.query_one(Rail)
        first = rail.marker
        await pilot.press("tab")
        for _ in range(12):
            await pilot.pause()

        assert rail.marker > first, "the marker should follow the cursor down"


async def test_the_marker_travels_rather_than_jumps(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless, animations resolve at once, so the travel cannot be watched.

    What can be checked is that it is asked for. `marker` is a float for exactly
    this reason -- Textual can only interpolate numbers, and a marker that could
    only hold whole rows could only blink between them.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    asked: list[str] = []

    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        for _ in range(24):
            await pilot.pause()

        rail = screen.query_one(Rail)
        animate = rail.animate

        def spy(attribute: str, *args: Any, **kwargs: Any) -> Any:
            asked.append(attribute)
            return animate(attribute, *args, **kwargs)

        monkeypatch.setattr(rail, "animate", spy)
        await pilot.press("tab")
        for _ in range(12):
            await pilot.pause()

    assert "marker" in asked, "the marker should be animated along the rail"


def _rail_column(app: FlexiApp, rail: Rail) -> list[tuple[str, str]]:
    """The glyph and the colour actually drawn on each row of the rail."""
    strips = app.screen._compositor.render_strips()
    column = rail.region.x + len(GUTTER)
    drawn: list[tuple[str, str]] = []
    for step in range(rail.region.height):
        at = 0
        for segment in strips[rail.region.y + step]:
            if at <= column < at + len(segment.text):
                style = segment.style
                triplet = style.color.triplet if style and style.color else None
                drawn.append(
                    (segment.text[column - at], triplet.hex.upper() if triplet else "")
                )
                break
            at += len(segment.text)
    return drawn


async def test_the_foot_of_the_rail_matches_the_line_above_it(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is structure, not content. A brighter one drew the eye to the end."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        for _ in range(24):
            await pilot.pause()

        drawn = _rail_column(app, screen.query_one(Rail))
        foot, hairline = drawn[-1], colour("c-line").upper()
        assert foot[0] == TAIL
        assert foot[1] == hairline


async def test_the_segment_holding_the_marker_is_lit(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A question is two rows: itself, and the space under it.

    Lighting the second picks out the whole segment the marker stands in, so the
    live question reads as a stretch of rail rather than as a point on it.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        for _ in range(24):
            await pilot.pause()

        rail = screen.query_one(Rail)
        drawn = _rail_column(app, rail)
        marker = round(rail.marker)

        assert drawn[marker][0] == MARK_LIVE
        assert drawn[marker][1] == colour("c-accent").upper()
        assert drawn[marker + 1][1] == colour("c-muted").upper(), "the segment under it"
        assert drawn[marker + 2][1] == colour("c-line").upper(), "and no further"
