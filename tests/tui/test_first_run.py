"""Install, launch, answer five questions, and get to the dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import time_machine
from textual.pilot import Pilot
from textual.widgets import Input, Select, Static

from flexi.app import FlexiApp
from flexi.components.wordmark import Wordmark
from flexi.models.database.db import Base
from flexi.models.database.engine import create_db_engine
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.settings import NO_DIVISION
from flexi.screens.setup import GUTTER, Question, Rail, SetupScreen, form_rows
from flexi.services.settings import SettingsService
from flexi.theme import MARK_LIVE, TAIL, colour
from tests.conftest import session_at
from tests.tui.conftest import WIDE, showing


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """A migrated database with nothing in it, as `run_migrations` leaves it."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


def notices(app: FlexiApp) -> list[str]:
    """Everything the application has put in front of the user, oldest first."""
    return [notification.message for notification in app._notifications]


async def revealed(pilot: Pilot[None]) -> None:
    """Wait for the setup screen's reveal to finish.

    `pause` drains the messages queued when it is called, so pumping it a fixed
    number of times samples the animation wherever the machine happens to be.
    An opacity of 0.99 renders `$c-accent` as `#00A9AC` instead of `#00AAAD`.

    The wait is on `wait_for_scheduled_animations`, which covers the delayed
    opacity animation as well as the marker row and the question height.
    """
    await pilot.wait_for_scheduled_animations()
    await pilot.pause()


async def _answer(app: FlexiApp, working_days: str) -> None:
    screen = showing(app, SetupScreen)
    screen.query_one("#input-leave-start", Input).value = "04-06"
    screen.query_one("#input-entitlement", Input).value = "28"
    screen.query_one("#input-working-days", Input).value = working_days
    screen.query_one("#select-division", Select).value = "scotland"
    screen.query_one("#input-auto-close", Input).value = "18:30"


async def test_fresh_database_opens_on_setup(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)


async def test_reasonable_answer_lands_on_the_dashboard(fresh_db: Path) -> None:
    """One spelling, one boot; spelling is `tests/services/test_working_days.py`."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        await pilot.pause()
        showing(app, SetupScreen).action_save()
        await pilot.pause()
        await pilot.pause()
        showing(app, DashboardScreen)


async def test_second_launch_goes_straight_to_the_dashboard(fresh_db: Path) -> None:
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


async def test_enter_in_the_last_field_finishes_the_form(fresh_db: Path) -> None:
    """The screen says "enter to save", so enter has to save."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        showing(app, SetupScreen).query_one("#input-auto-close", Input).focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        showing(app, DashboardScreen)


async def test_half_answered_form_is_not_saved(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        screen.query_one("#input-entitlement", Input).value = ""
        await pilot.pause()

        screen.action_save()
        await pilot.pause()

        assert "All fields are required" in notices(app)
        showing(app, SetupScreen)

    with session_at(fresh_db) as session:
        assert SettingsService(session).get_settings() is None


async def test_non_numeric_entitlement_is_refused(fresh_db: Path) -> None:
    """The answer is read before anything is written, the settings included."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        screen.query_one("#input-entitlement", Input).value = "twenty-five"
        await pilot.pause()

        screen.action_save()
        await pilot.pause()

        assert any(
            "Entitlement must be a number of days" in notice for notice in notices(app)
        )
        showing(app, SetupScreen)

    with session_at(fresh_db) as session:
        assert SettingsService(session).get_settings() is None


@pytest.mark.parametrize("value", ["-1", "nan", "inf"])
async def test_entitlement_outside_the_domain_is_refused(
    fresh_db: Path, value: str
) -> None:
    """A parseable float is not necessarily a meaningful leave allowance."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        screen.query_one("#input-entitlement", Input).value = value

        screen.action_save()
        await pilot.pause()

        assert any("finite and zero or more" in notice for notice in notices(app))
        showing(app, SetupScreen)

    with session_at(fresh_db) as session:
        assert SettingsService(session).get_settings() is None


async def test_cleared_region_is_asked_for_again(fresh_db: Path) -> None:
    """Absence cannot be booked until the division is known, and it has no default."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        screen.query_one("#select-division", Select).clear()
        await pilot.pause()

        screen.action_save()
        await pilot.pause()

        assert NO_DIVISION in notices(app)
        showing(app, SetupScreen)

    with session_at(fresh_db) as session:
        assert SettingsService(session).get_settings() is None


async def test_setup_refuses_an_answer_it_cannot_read(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "whenever")
        await pilot.pause()
        showing(app, SetupScreen).action_save()
        await pilot.pause()

        showing(app, SetupScreen)  # still here, not dismissed

    with session_at(fresh_db) as session:
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

    with session_at(fresh_db) as session:
        settings = SettingsService(session)
        stored = settings.get_settings()
        assert stored is not None
        assert stored.leave_year_start == "04-06"
        assert stored.bank_holiday_division == "scotland"
        assert stored.auto_close_time == "18:30"
        assert settings.get_working_day_indices() == [1, 2, 3]
        assert settings.get_active_entitlement_days(None) == 28.0


# ---- the year the allowance is filed under ----

FEBRUARY = datetime(2026, 2, 16, 10, 0, tzinfo=UTC)
"""A day the two leave years disagree about: 2026 from 1 January, 2025 from 6 April."""


def entitlement_note(app: FlexiApp) -> str:
    """The sentence under the entitlement field."""
    ask = showing(app, SetupScreen).query_one("#ask-entitlement", Question)
    return str(ask.query_one(".note", Static).render())


def filed(db_path: Path) -> list[tuple[int, float]]:
    """Every entitlement in the database, by year."""
    with session_at(db_path) as session:
        return [
            (row.year, row.days) for row in SettingsService(session).all_entitlements()
        ]


async def test_note_names_the_year_days_are_filed_under(
    fresh_db: Path,
) -> None:
    """The note and the save both read the leave year start on the form.

    The form offers 6 April and the stored default is 1 January, so in February
    the two settings name different years.
    """
    with time_machine.travel(FEBRUARY, tick=False):
        app = FlexiApp(db_path=fresh_db)
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            assert entitlement_note(app) == "days for 2025, halves allowed"

            await _answer(app, "Mon-Fri")
            await pilot.pause()
            showing(app, SetupScreen).action_save()
            await pilot.pause()
            await pilot.pause()

    assert filed(fresh_db) == [(2025, 28.0)]


async def test_note_follows_the_start_that_is_typed(fresh_db: Path) -> None:
    """A leave year running with the calendar files February under this year."""
    with time_machine.travel(FEBRUARY, tick=False):
        app = FlexiApp(db_path=fresh_db)
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            await _answer(app, "Mon-Fri")
            screen = showing(app, SetupScreen)
            screen.query_one("#input-leave-start", Input).value = "01-01"
            await pilot.pause()
            assert entitlement_note(app) == "days for 2026, halves allowed"

            screen.action_save()
            await pilot.pause()
            await pilot.pause()

    assert filed(fresh_db) == [(2026, 28.0)]


async def test_half_typed_start_leaves_the_note_alone(fresh_db: Path) -> None:
    """Half a date is not an answer yet, and a note that flashes is noise."""
    with time_machine.travel(FEBRUARY, tick=False):
        app = FlexiApp(db_path=fresh_db)
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            field = showing(app, SetupScreen).query_one("#input-leave-start", Input)
            field.value = "01-01"
            await pilot.pause()

            field.value = "01-"
            await pilot.pause()

            assert entitlement_note(app) == "days for 2026, halves allowed"


async def test_wordmark_lands_and_the_questions_arrive(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Screen.dismiss` pops the top of the stack, so the wordmark is a widget here."""
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

    with session_at(fresh_db) as session:
        stored = SettingsService(session).get_settings()
        assert stored is not None, "the answers survived the animation"
        assert stored.leave_year_start == "04-06"


@pytest.mark.parametrize("key", ["f5", "x", "space"])
async def test_any_key_cuts_the_animation_short(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Setting Flexi up again should not mean sitting through the word.

    A printable key can fail both ways: the leave-year field has focus from the
    moment the screen mounts, so the key must skip and not be typed into it.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        questions = showing(app, SetupScreen).query_one("#setup-questions")
        assert not questions.has_class("-arrived")

        await pilot.press(key)
        await revealed(pilot)

        assert questions.has_class("-arrived"), "the word stopped and let them in"
        assert app.screen.query_one("#input-leave-start", Input).value == "04-06", (
            "and the key that skipped it was not typed into anything"
        )


async def test_quit_key_quits_during_the_animation(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every key is stopped at the screen so that the skip does nothing else.

    ctrl+q is the exception, or the documented quit key takes two presses.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)

        await pilot.press("ctrl+q")
        await pilot.pause()

        assert not app.is_running


async def test_tab_moves_between_the_questions_once_they_are_up(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Printable characters are claimed by the focused field, so tab is the proof."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        await revealed(pilot)
        assert app.screen.focused is screen.query_one("#input-leave-start", Input)

        await pilot.press("tab")
        for _ in range(12):
            await pilot.pause()

        assert app.screen.focused is screen.query_one("#input-entitlement", Input)


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
async def test_wordmark_is_centred_over_the_questions(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    """A widget narrower than its column sits against the left edge of it."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(width, 34)) as pilot:
        await pilot.pause()
        showing(app, SetupScreen).query_one(Wordmark).skip()
        await revealed(pilot)

        left, right = _logo_span(app)
        questions = app.screen.query_one("#setup-questions").region
        assert abs((left + right) / 2 - (questions.x + questions.width / 2)) <= 1


async def test_wordmark_does_not_move_sideways_on_the_reveal(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reveal widens the column under the logo, which stays where it is."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 34)) as pilot:
        await pilot.pause()
        wordmark = showing(app, SetupScreen).query_one(Wordmark)
        before = wordmark.region.x

        wordmark.skip()
        await revealed(pilot)

        assert wordmark.region.x == before


async def test_wordmark_rises_to_make_room(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        await revealed(pilot)

        assert wordmark.region.y < settled, "the logo should have moved up"
        assert questions.region.height > 0


async def test_counted_height_is_the_real_one(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rise animates to a counted height, because measuring means a flash.

    Counting is only safe while `form_rows` agrees with the stylesheet.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        await revealed(pilot)

        counted = form_rows(len(screen.query(Question)))
        assert screen.query_one("#setup-questions").region.height == counted


async def test_questions_open_out_instead_of_appearing(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless, animations resolve at once, so the rise cannot be watched.

    `test_wordmark_rises_to_make_room` checks where everything ends up and
    passes either way; what can be checked here is that the rise is asked for.
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


async def test_rail_is_one_unbroken_line(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rail drawn a piece per question has a gap wherever the rows are spaced."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        await revealed(pilot)

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


async def test_marker_sits_on_the_focused_question(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        await revealed(pilot)

        rail = screen.query_one(Rail)
        first = rail.marker
        await pilot.press("tab")
        for _ in range(12):
            await pilot.pause()

        assert rail.marker > first, "the marker should follow the cursor down"


async def test_marker_travels_instead_of_jumping(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless, animations resolve at once, so the travel cannot be watched.

    `marker` is a float because Textual interpolates numbers; whole rows blink.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    asked: list[str] = []

    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        await revealed(pilot)

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


async def test_foot_of_the_rail_matches_the_line_above(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tail is structure, not content, so it carries the hairline colour."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        await revealed(pilot)

        drawn = _rail_column(app, screen.query_one(Rail))
        foot, hairline = drawn[-1], colour("c-line").upper()
        assert foot[0] == TAIL
        assert foot[1] == hairline


async def test_only_the_marker_is_lit_on_the_rail(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(112, 40)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        await revealed(pilot)

        rail = screen.query_one(Rail)
        drawn = _rail_column(app, rail)
        marker = round(rail.marker)

        hairline = colour("c-line").upper()
        assert drawn[marker][0] == MARK_LIVE
        assert drawn[marker][1] == colour("c-accent").upper()
        assert drawn[marker + 1][1] == hairline, "nothing lit under the marker"
        assert {tone for _, tone in drawn[1:-1] if tone != drawn[marker][1]} == {
            hairline
        }, "one weight for the whole line"
