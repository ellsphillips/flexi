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
from flexi.models.database.db import Base
from flexi.models.database.engine import create_db_engine, get_session
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.settings import NO_DIVISION
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


def notices(app: FlexiApp) -> list[str]:
    """Everything the application has put in front of the user, oldest first."""
    return [notification.message for notification in app._notifications]


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


async def test_setup_accepts_a_reasonable_answer_and_lands_on_the_dashboard(
    fresh_db: Path,
) -> None:
    """One spelling, one boot.

    This was parametrised over three spellings of a working week, spending two
    extra full application boots to re-assert string parsing that
    `tests/services/test_working_days.py` pins exhaustively in microseconds.
    What the boot is here to prove is that an answer reaches the database and
    the app moves on, and one answer proves that.
    """
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
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


async def test_enter_in_the_last_field_is_the_way_to_finish(fresh_db: Path) -> None:
    """The screen says "enter to save", so enter has to save.

    Every other question is left with tab, and reaching for ctrl+s at the end
    of a form that has just told you which key finishes it is the kind of
    small betrayal nobody reports.
    """
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


async def test_a_half_answered_form_is_not_saved(fresh_db: Path) -> None:
    """Five questions, and a blank one is unanswered rather than defaulted.

    Guessing at an entitlement nobody typed would be filed under a leave year
    and then quietly spent against.
    """
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

    with get_session(create_db_engine(fresh_db)) as session:
        assert SettingsService(session).get_settings() is None


async def test_an_entitlement_that_is_not_a_number_is_refused(fresh_db: Path) -> None:
    """An entitlement spelled out in words is unusable, however reasonable.

    The settings themselves would save perfectly happily, leaving somebody set
    up with no entitlement and a screen that had said nothing about it — so the
    answer is read before anything at all is written.
    """
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

    with get_session(create_db_engine(fresh_db)) as session:
        assert SettingsService(session).get_settings() is None


@pytest.mark.parametrize("value", ["-1", "nan", "inf"])
async def test_an_entitlement_outside_the_domain_is_refused(
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

    with get_session(create_db_engine(fresh_db)) as session:
        assert SettingsService(session).get_settings() is None


async def test_a_cleared_region_is_asked_for_again(fresh_db: Path) -> None:
    """The bank holiday calendar is the one answer with no sensible default.

    Absence cannot be booked at all until the division is known, so an empty
    select has to come back as a question rather than be filed as "nowhere".

    The wording is the settings screen's, imported rather than repeated: the two
    forms asked the same question and refused it in two different sentences.
    """
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

    with get_session(create_db_engine(fresh_db)) as session:
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


async def test_a_key_the_questions_do_not_claim_cuts_the_animation_short(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A splash that cannot be skipped is a splash that is in the way.

    Somebody setting Flexi up a second time — a new machine, a reset database —
    should not have to sit through the word turning in.

    Not *any* key, despite what the screen's own docstring says: the leave-year
    field is focused from the moment the screen mounts, so every printable key
    is claimed by an Input that is still clipped to nothing and the screen never
    sees it. F5 is a key nothing underneath wants, so it is the one that gets
    through. The second assertion is the half of that which is a real hazard —
    the skip key must not end up typed into the invisible first answer.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        questions = showing(app, SetupScreen).query_one("#setup-questions")
        assert not questions.has_class("-arrived")

        await pilot.press("f5")
        for _ in range(24):
            await pilot.pause()

        assert questions.has_class("-arrived"), "the word stopped and let them in"
        assert app.screen.query_one("#input-leave-start", Input).value == "04-06", (
            "and the key that skipped it was not typed into anything"
        )


async def test_once_the_questions_are_up_tab_moves_between_them_again(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The skip is not allowed to go on eating keystrokes.

    Tab is the key that proves it, and the only key that can: printable
    characters are claimed by the focused field and never reach the screen at
    all, so tab is the one the skip could still be swallowing. A form that
    cannot be moved through is a worse first run than an animation nobody can
    skip.
    """
    monkeypatch.setattr("flexi.components.wordmark.wanted", lambda **_: True)
    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one(Wordmark).skip()
        for _ in range(24):
            await pilot.pause()
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


async def test_the_marker_is_the_only_thing_lit_on_the_rail(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rail is one weight from top to bottom, and the diamond sits on it.

    Lighting the row beneath the marker as well, to pick out the two-row segment
    a question occupies, made the rail busier without saying anything the
    diamond had not already said.
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

        hairline = colour("c-line").upper()
        assert drawn[marker][0] == MARK_LIVE
        assert drawn[marker][1] == colour("c-accent").upper()
        assert drawn[marker + 1][1] == hairline, "nothing lit under the marker"
        assert {tone for _, tone in drawn[1:-1] if tone != drawn[marker][1]} == {
            hairline
        }, "one weight for the whole line"
