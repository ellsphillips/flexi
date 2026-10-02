"""Install, launch, answer six questions, and get to the dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import time_machine
from textual.notifications import Notification
from textual.pilot import Pilot
from textual.widgets import Input, Label, Select, Static

from flexi.app import FlexiApp
from flexi.components.wordmark import Wordmark
from flexi.models.database.engine import create_db_engine
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.settings import ALL_REQUIRED, NO_DIVISION
from flexi.screens.setup import (
    GUTTER,
    LEAVE_YEAR_START,
    Question,
    Rail,
    SetupScreen,
    form_rows,
)
from flexi.services.settings import SettingsService, read_leave_year_start
from flexi.theme import MARK_LIVE, TAIL, colour
from tests.conftest import session_at
from tests.database import create_schema
from tests.tui.conftest import WIDE, screen_text, showing


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """A migrated database with nothing in it, as `run_migrations` leaves it."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    create_schema(engine)
    engine.dispose()
    return path


def notices(app: FlexiApp) -> list[str]:
    """Everything the application has put in front of the user, oldest first."""
    return [notification.message for notification in app._notifications]


def refusals(app: FlexiApp) -> list[Notification]:
    """The errors put in front of the user, oldest first."""
    return [shown for shown in app._notifications if shown.severity == "error"]


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
    screen.query_one("#input-leave-start", Input).value = "6 Apr"
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
        assert screen.focused is screen.query_one("#select-division", Select)

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


# ---- the leave-year start ----


def test_the_start_offered_is_6_april() -> None:
    """Offered in words: `04-06` reads two ways, and the form would refuse it."""
    assert read_leave_year_start(LEAVE_YEAR_START) == (4, 6)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("1 April", "04-01"), ("1st Sep", "09-01"), ("30/09", "09-30")],
)
async def test_the_start_is_saved_month_first_however_it_is_written(
    fresh_db: Path, typed: str, stored: str
) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        screen.query_one("#input-leave-start", Input).value = typed

        screen.action_save()
        await pilot.pause()
        await pilot.pause()

        showing(app, DashboardScreen)

    with session_at(fresh_db) as session:
        row = SettingsService(session).get_settings()
        assert row is not None
        assert row.leave_year_start == stored


async def test_a_start_that_reads_two_ways_is_refused(fresh_db: Path) -> None:
    """`01/04` was saved as 4 January, under a dashboard that dates day first."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        field = screen.query_one("#input-leave-start", Input)
        field.value = "01/04"

        screen.action_save()
        await pilot.pause()

        [refused] = refusals(app)
        assert refused.title == "Leave year starts"
        assert refused.message == (
            "'01/04' could be 1 April or 4 January: type 1 Apr or 4 Jan"
        )
        assert screen.focused is field

    with session_at(fresh_db) as session:
        assert SettingsService(session).get_settings() is None


async def test_hours_a_day_are_what_every_day_expects(fresh_db: Path) -> None:
    """7:24 is offered, and a decimal is hours: 7.5 is half past, not 7:05."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        field = screen.query_one("#input-hours", Input)
        assert field.value == "7:24"
        field.value = "7.5"
        await pilot.pause()

        screen.action_save()
        await pilot.pause()
        await pilot.pause()

        showing(app, DashboardScreen)
        assert "0:00 of 7:30" in screen_text(app), "today expects the answer"

    with session_at(fresh_db) as session:
        assert SettingsService(session).get_contracted() == timedelta(minutes=450)


@pytest.mark.parametrize(
    ("typed", "said"),
    [
        ("", ALL_REQUIRED),
        ("0", "more than 0:00"),
        ("25", "no more than 24:00"),
        ("seven", "not a length of time"),
        ("7.24", "not a whole number of minutes"),
        ("7.30", "could mean 7:30"),
    ],
)
async def test_hours_a_day_that_cannot_be_used_are_refused(
    fresh_db: Path, typed: str, said: str
) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        screen.query_one("#input-hours", Input).value = typed

        screen.action_save()
        await pilot.pause()

        assert any(said in notice for notice in notices(app))
        showing(app, SetupScreen)

    with session_at(fresh_db) as session:
        assert SettingsService(session).get_settings() is None


@pytest.mark.parametrize(
    ("selector", "typed", "asked"),
    [
        ("#input-leave-start", "1 Apirl", "Leave year starts"),
        ("#input-entitlement", "twenty-five", "Annual entitlement"),
        ("#input-working-days", "whenever", "Working days"),
        ("#input-hours", "7.30", "Hours a day"),
        ("#input-auto-close", "half six", "Auto-close at"),
    ],
)
async def test_refusal_names_the_question_and_goes_back_to_it(
    fresh_db: Path, selector: str, typed: str, asked: str
) -> None:
    """The cursor stayed where enter was pressed, and the message named no question.

    It goes back to the answer instead, with it selected to type over, and
    every other answer is left as it was.
    """
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        field = screen.query_one(selector, Input)
        field.value = typed
        screen.query_one(Select).focus()
        await pilot.pause()
        answers = {each.id: each.value for each in screen.query(Input)}

        await pilot.press("ctrl+s")
        await pilot.pause()

        [refused] = refusals(app)
        assert refused.title == asked
        assert screen.focused is field
        assert field.selected_text == typed
        assert {each.id: each.value for each in screen.query(Input)} == answers

    with session_at(fresh_db) as session:
        assert SettingsService(session).get_settings() is None


async def test_the_first_answer_refused_is_the_first_asked(fresh_db: Path) -> None:
    """Hours a day were read before the leave year, so two typos walked backwards."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        screen = showing(app, SetupScreen)
        screen.query_one("#input-leave-start", Input).value = "1 Apirl"
        screen.query_one("#input-hours", Input).value = "7.30"

        screen.action_save()
        await pilot.pause()

        [refused] = refusals(app)
        assert refused.title == "Leave year starts"
        assert screen.focused is screen.query_one("#input-leave-start", Input)


async def test_heading_counts_the_questions(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await revealed(pilot)
        screen = showing(app, SetupScreen)
        assert len(screen.query(Question)) == 6
        assert "Six questions" in screen_text(app)


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
        assert app.screen.query_one("#input-leave-start", Input).value == (
            LEAVE_YEAR_START
        ), "and the key that skipped it was not typed into anything"


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


async def test_q_is_typed_into_an_answer(fresh_db: Path) -> None:
    """The dashboard quits on q; a question takes it as a letter."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await revealed(pilot)
        field = showing(app, SetupScreen).query_one("#input-working-days", Input)
        field.focus()
        field.value = ""

        await pilot.press("q")
        await pilot.pause()

        assert app.is_running
        assert field.value == "q"


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


# ---- a terminal too short for the spaced form ----


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_every_question_is_on_screen_and_reached_by_tab(
    fresh_db: Path, size: tuple[int, int]
) -> None:
    """Twenty-four rows is shorter than the wordmark over the spaced-out form.

    The row under each question goes first, so nothing waits below the fold
    and tab still reaches every question in turn.
    """
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=size) as pilot:
        await revealed(pilot)
        screen = showing(app, SetupScreen)
        assert screen.max_scroll_y == 0, "nothing should wait below the fold"

        drawn = screen_text(app)
        for question in screen.query(Question):
            ask = str(question.query_one(".ask", Label).render())
            assert ask in drawn
            focused = screen.focused
            assert focused is not None
            assert question in focused.ancestors_with_self, f"tab missed {ask}"
            await pilot.press("tab")
            await pilot.pause()


async def test_resizing_closes_the_form_up_and_opens_it_out(fresh_db: Path) -> None:
    """The rail and the reveal are counted, so a resize has to count them again."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await revealed(pilot)
        screen = showing(app, SetupScreen)
        block = screen.query_one("#setup-questions")
        spaced = block.region.height

        await pilot.resize_terminal(80, 24)
        await revealed(pilot)
        assert block.region.height < spaced
        assert screen.query_one(Rail).region.height == block.region.height
        assert screen.max_scroll_y == 0

        await pilot.resize_terminal(120, 40)
        await revealed(pilot)
        assert block.region.height == spaced
        assert screen.query_one(Rail).region.height == spaced


async def test_marker_steps_one_row_a_question_when_closed_up(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The questions arrive closed up, and the marker lands beside the next one."""
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        await revealed(pilot)

        await pilot.press("tab")
        for _ in range(12):
            await pilot.pause()
        await revealed(pilot)

        rail = screen.query_one(Rail)
        entitlement = screen.query_one("#ask-entitlement", Question)
        assert rail.region.y + round(rail.marker) == entitlement.region.y
        assert " " not in "".join(glyph for glyph, _ in _rail_column(app, rail))


# ---- a terminal eighty columns wide ----


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_every_note_is_drawn_whole(fresh_db: Path, size: tuple[int, int]) -> None:
    """Eighty columns is the size a terminal opens at on macOS and Linux.

    A form wider than that loses the end of every long note off the right edge,
    mid-word.
    """
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=size) as pilot:
        await revealed(pilot)
        screen = showing(app, SetupScreen)
        assert screen.query_one("#setup").region.right <= size[0]

        drawn = screen_text(app)
        for question in screen.query(Question):
            note = str(question.query_one(".note", Static).render())
            assert note in drawn, f"{note!r} is cut short"


async def test_regions_are_listed_whole_in_the_narrow_field(fresh_db: Path) -> None:
    """The list is as wide as its field, and one column less wraps a region."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=(80, 24)) as pilot:
        await revealed(pilot)
        showing(app, SetupScreen).query_one(Select).action_show_overlay()
        await pilot.pause()

        assert "Northern Ireland" in screen_text(app)
