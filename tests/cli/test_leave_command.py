"""Booking leave in one line, and being shown it before it is written."""

from __future__ import annotations

from datetime import date, datetime

import click
import pytest
from sqlalchemy.orm import Session

from flexi.cli.leave import Request, parse_request, render, run
from flexi.constants import AbsenceType, Portion
from flexi.models.database.db import AbsenceDay, BankHolidayCache, BankHolidayRefresh
from flexi.services.registry import Services, build_services
from flexi.services.settings import parse_settings

MONDAY = date(2026, 8, 10)
TUESDAY = date(2026, 8, 11)
FRIDAY = date(2026, 8, 14)
BANK_HOLIDAY = date(2026, 8, 31)


@pytest.fixture
def services(session: Session) -> Services:
    built = build_services(session)
    built.settings.save_settings(
        parse_settings(
            leave_year_start="10-20",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    built.settings.save_entitlement(2025, 25.0)
    session.add_all(
        (
            BankHolidayRefresh(
                division="england-and-wales",
                fetched_at=datetime(2026, 1, 1, 9, 0),
            ),
            BankHolidayCache(
                division="england-and-wales",
                date=BANK_HOLIDAY,
                title="Summer bank holiday",
            ),
        )
    )
    session.commit()
    return build_services(session)


def _booked(session: Session) -> list[AbsenceDay]:
    return session.query(AbsenceDay).order_by(AbsenceDay.date).all()


# The grammar ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("words", "kind", "portion", "when"),
    [
        (("annual", "friday"), AbsenceType.ANNUAL, None, "friday"),
        (("sick", "today", "pm"), AbsenceType.SICK, Portion.PM, "today"),
        (("sick", "today", "afternoon"), AbsenceType.SICK, Portion.PM, "today"),
        (("toil", "12", "jun"), AbsenceType.FLEXI, None, "12 jun"),
        (
            ("annual", "monday", "to", "friday"),
            AbsenceType.ANNUAL,
            None,
            "monday to friday",
        ),
        (
            ("annual", "monday", "to", "friday", "am"),
            AbsenceType.ANNUAL,
            Portion.AM,
            "monday to friday",
        ),
        (("cancel", "friday"), None, None, "friday"),
        (("annual",), AbsenceType.ANNUAL, None, ""),
    ],
)
def test_grammar_splits_into_kind_portion_and_when(
    words: tuple[str, ...],
    kind: AbsenceType | None,
    portion: Portion | None,
    when: str,
) -> None:
    """The kind comes back resolved. `None` is a cancellation, and only that."""
    assert parse_request(words) == Request(kind, portion, when)


def test_portion_is_only_taken_from_the_end() -> None:
    """A month name or a note cannot be mistaken for a portion."""
    assert parse_request(("annual", "1", "may"))[1] is None


@pytest.mark.parametrize("word", ["someday", "vacation", "holidays"])
def test_unknown_kind_is_refused_by_name(word: str) -> None:
    with pytest.raises(click.UsageError, match=word):
        parse_request((word, "friday"))


@pytest.mark.parametrize(
    ("word", "portion"),
    [
        ("am", Portion.AM),
        ("morning", Portion.AM),
        ("pm", Portion.PM),
        ("afternoon", Portion.PM),
    ],
)
def test_four_portion_words_are_accepted(word: str, portion: Portion) -> None:
    assert parse_request(("annual", "friday", word)).portion is portion


def test_half_is_not_a_portion_word() -> None:
    """Half names no half, so it falls through to the date the command refuses."""
    asked = parse_request(("annual", "friday", "half"))

    assert asked.portion is None
    assert asked.when == "friday half"


# Planning and confirming ----------------------------------------------------


def test_dry_run_writes_nothing(services: Services, session: Session) -> None:
    code = run(
        services,
        ("annual", "monday", "to", "friday"),
        note=None,
        assume_yes=False,
        dry_run=True,
        today=MONDAY,
    )
    assert code == 0
    assert _booked(session) == []


def test_yes_books_without_asking(services: Services, session: Session) -> None:
    code = run(
        services,
        ("annual", "monday", "to", "friday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    assert code == 0
    assert [row.date for row in _booked(session)] == [
        MONDAY + __import__("datetime").timedelta(days=n) for n in range(5)
    ]


def test_half_day_books_the_afternoon(services: Services, session: Session) -> None:
    run(
        services,
        ("sick", "today", "pm"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    booked = _booked(session)
    assert len(booked) == 1
    assert booked[0].portion is Portion.PM
    assert booked[0].absence_type is AbsenceType.SICK


def test_other_leave_insists_on_a_note(services: Services) -> None:
    import click

    with pytest.raises(click.UsageError, match="note"):
        run(
            services,
            ("other", "friday"),
            note=None,
            assume_yes=True,
            dry_run=False,
            today=MONDAY,
        )


def test_backwards_range_is_refused_before_planning(services: Services) -> None:
    import click

    with pytest.raises(click.UsageError, match="runs backwards"):
        run(
            services,
            ("annual", "2026-09-10", "to", "2026-08-10"),
            note=None,
            assume_yes=True,
            dry_run=False,
            today=MONDAY,
        )


def test_weekend_only_span_books_nothing(services: Services, session: Session) -> None:
    code = run(
        services,
        ("annual", "2026-08-15", "to", "2026-08-16"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    assert code == 1
    assert _booked(session) == []


# What the confirmation says -------------------------------------------------


def test_render_names_the_bank_holiday(services: Services) -> None:
    plan = services.absence.plan(
        date(2026, 8, 28), date(2026, 9, 2), AbsenceType.ANNUAL
    )
    shown = render(plan)

    assert "Summer bank holiday" in shown
    assert "not a working day" in shown
    assert "3 days" in shown


def test_holiday_title_cannot_carry_terminal_escapes(
    services: Services, session: Session
) -> None:
    """The title comes from GOV.UK and is echoed at the screen.

    Click strips the CSI form and nothing else, so an OSC window title and a
    carriage return in a tampered calendar would reach the terminal intact.
    """
    session.add(
        BankHolidayCache(
            division="england-and-wales",
            date=TUESDAY,
            title="Bank Holiday\x1b[31m PWNED \x1b]0;owned\x07\x1b[0m\r\nfake line",
        )
    )
    session.commit()
    built = build_services(session)

    shown = render(built.absence.plan(TUESDAY, TUESDAY, AbsenceType.ANNUAL))

    assert "PWNED" in shown, "the words survive; the instructions do not"
    assert "\x1b" not in shown
    assert "\x07" not in shown
    assert "\r" not in shown


def test_render_shows_the_allowance_moving(services: Services) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert "25 → 20 days left" in render(plan)


def test_render_keeps_cross_year_allowances_separate(
    services: Services,
) -> None:
    services.settings.save_settings(
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    services.settings.save_entitlement(2026, 1.0)
    services.settings.save_entitlement(2027, 2.0)

    plan = services.absence.plan(
        date(2026, 12, 30), date(2027, 1, 5), AbsenceType.ANNUAL
    )

    shown = render(plan)
    assert "Annual leave 2026: 1 → 0 days left" in shown
    assert "Annual leave 2027: 2 → 0 days left" in shown


def test_stale_confirmation_is_reported_as_failure(
    services: Services,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A changed plan is not a green message and a successful exit code."""

    def change_the_day(*_args: object, **_kwargs: object) -> bool:
        assert services.absence.book(MONDAY, AbsenceType.SICK).success
        return True

    monkeypatch.setattr("click.confirm", change_the_day)

    code = run(
        services,
        ("annual", "monday"),
        note=None,
        assume_yes=False,
        dry_run=False,
        today=MONDAY,
    )

    assert code == 1
    assert "changed" in capsys.readouterr().err, "a failure is not the output"
    assert [(row.date, row.absence_type) for row in _booked(session)] == [
        (MONDAY, AbsenceType.SICK)
    ]


def test_annual_leave_does_not_warn_about_flexi(
    services: Services,
) -> None:
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.ANNUAL, available_toil_days=-90.0
    )
    assert "deficit" not in render(plan)


def test_taking_toil_beyond_the_balance_warns(services: Services) -> None:
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=2.0
    )
    assert "deficit" in render(plan)


# Cancelling -----------------------------------------------------------------


def test_cancelling_removes_what_was_booked(
    services: Services, session: Session
) -> None:
    run(
        services,
        ("annual", "monday", "to", "friday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    assert len(_booked(session)) == 5

    code = run(
        services,
        ("cancel", "monday", "to", "friday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    assert code == 0
    assert _booked(session) == []


def test_cancelling_nothing_says_so(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(
        services,
        ("cancel", "friday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    assert code == 1
    assert "Nothing is booked on" in capsys.readouterr().err


def test_cancelling_half_a_full_day_names_the_booking(
    services: Services, session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    """A full booking matches neither half filter."""
    services.absence.book(MONDAY, AbsenceType.ANNUAL)

    code = run(
        services,
        ("cancel", "monday", "am"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )

    assert code == 1
    reported = capsys.readouterr().err
    assert "Nothing is booked on" not in reported
    assert "Annual leave" in reported
    assert "Cancel the whole day" in reported
    assert len(_booked(session)) == 1


def test_cancelling_half_an_empty_day_says_so(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(
        services,
        ("cancel", "monday", "am"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )

    assert code == 1
    assert "Nothing is booked on" in capsys.readouterr().err


def test_cancelling_is_a_dry_run_too(services: Services, session: Session) -> None:
    run(
        services,
        ("annual", "friday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    run(
        services,
        ("cancel", "friday"),
        note=None,
        assume_yes=False,
        dry_run=True,
        today=MONDAY,
    )
    assert len(_booked(session)) == 1


def test_cancelling_one_half_preserves_the_other(
    services: Services,
    session: Session,
) -> None:
    services.absence.book(MONDAY, AbsenceType.ANNUAL, Portion.AM)
    services.absence.book(MONDAY, AbsenceType.SICK, Portion.PM)

    code = run(
        services,
        ("cancel", "monday", "pm"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )

    assert code == 0
    assert [(row.absence_type, row.portion) for row in _booked(session)] == [
        (AbsenceType.ANNUAL, Portion.AM)
    ]


def test_no_arguments_lists_the_kinds() -> None:
    """The first word comes from a closed list, so the answer is the vocabulary."""
    import click

    with pytest.raises(click.UsageError, match="annual, sick, toil, unpaid, other"):
        parse_request(())


# Refusals in the confirmation -----------------------------------------------


def test_already_booked_day_is_shown_as_refused(
    services: Services, session: Session
) -> None:
    """A refusal is not a skip.

    A weekend is passed over unmeant; a clash is a day that was meant and cannot
    be had, so it carries the reason.
    """
    run(
        services,
        ("annual", "monday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )

    shown = render(services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL))

    assert "✗" in shown, "the clash is marked as turned down, not passed over"
    assert "4 days" in shown, "and the rest of the week is still bookable"


# Being asked before anything is written -------------------------------------


def refusing(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Answer no to every confirmation, recording what was asked."""
    asked: list[tuple[str, bool]] = []

    def answer(prompt: str, *, default: bool = False, **_: object) -> bool:
        asked.append((prompt.strip(), default))
        return False

    monkeypatch.setattr("click.confirm", answer)
    return asked


def test_declining_the_booking_writes_nothing(
    services: Services, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is no: a bare return after a block of text books nothing."""
    asked = refusing(monkeypatch)

    code = run(
        services,
        ("annual", "monday", "to", "friday"),
        note=None,
        assume_yes=False,
        dry_run=False,
        today=MONDAY,
    )

    assert code == 1
    assert _booked(session) == []
    assert asked == [("Book it?", False)]


def test_declining_the_cancellation_keeps_the_leave(
    services: Services, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling loses a booking, so backing out has to keep it."""
    run(
        services,
        ("annual", "monday", "to", "friday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    asked = refusing(monkeypatch)

    code = run(
        services,
        ("cancel", "monday", "to", "friday"),
        note=None,
        assume_yes=False,
        dry_run=False,
        today=MONDAY,
    )

    assert code == 1
    assert len(_booked(session)) == 5
    assert asked == [("Cancel these?", False)]


def test_cancellation_refuses_a_booking_added_after_confirmation(
    services: Services, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirmation approves exact rows, never everything currently in a span."""
    run(
        services,
        ("annual", "monday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )

    def add_then_confirm(
        _prompt: str, *, default: bool = False, **_options: object
    ) -> bool:
        assert default is False
        assert services.absence.book(TUESDAY, AbsenceType.SICK).success
        return True

    monkeypatch.setattr("click.confirm", add_then_confirm)

    code = run(
        services,
        ("cancel", "monday", "to", "friday"),
        note=None,
        assume_yes=False,
        dry_run=False,
        today=MONDAY,
    )

    assert code == 1
    assert [(row.date, row.absence_type) for row in _booked(session)] == [
        (MONDAY, AbsenceType.ANNUAL),
        (TUESDAY, AbsenceType.SICK),
    ]


def test_refused_cancellation_is_red(
    services: Services,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Yellow is the tone for a warning the CLI carried on past, and this is not."""
    run(
        services,
        ("annual", "monday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )

    def add_then_confirm(
        _prompt: str, *, default: bool = False, **_options: object
    ) -> bool:
        assert default is False
        assert services.absence.book(TUESDAY, AbsenceType.SICK).success
        return True

    monkeypatch.setattr("click.confirm", add_then_confirm)
    capsys.readouterr()

    with click.Context(click.Command("leave"), color=True):
        code = run(
            services,
            ("cancel", "monday", "to", "friday"),
            note=None,
            assume_yes=False,
            dry_run=False,
            today=MONDAY,
        )

    assert code == 1
    assert "\x1b[31m" in capsys.readouterr().err
