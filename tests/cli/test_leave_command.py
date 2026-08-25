"""Booking leave in one line, and being shown it before it is written."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.orm import Session

from flexi.cli.leave import Request, parse_request, render, run
from flexi.constants import AbsenceType, Portion
from flexi.models.database.db import AbsenceDay, BankHolidayCache
from flexi.services.registry import Services

MONDAY = date(2026, 8, 10)
FRIDAY = date(2026, 8, 14)
BANK_HOLIDAY = date(2026, 8, 31)


@pytest.fixture
def services(session: Session) -> Services:
    built = Services.build(session)
    built.settings.save_settings(
        leave_year_start="10-20",
        working_days="Mon-Fri",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    built.settings.save_entitlement(2025, 25.0)
    session.add(
        BankHolidayCache(
            division="england-and-wales",
            date=BANK_HOLIDAY,
            title="Summer bank holiday",
            fetched_at=datetime(2026, 1, 1, 9, 0),
        )
    )
    session.commit()
    return Services.build(session)


def _booked(session: Session) -> list[AbsenceDay]:
    return session.query(AbsenceDay).order_by(AbsenceDay.date).all()


# -- the grammar -------------------------------------------------------------


@pytest.mark.parametrize(
    ("words", "kind", "portion", "when"),
    [
        (("annual", "friday"), AbsenceType.ANNUAL, Portion.FULL, "friday"),
        (("sick", "today", "pm"), AbsenceType.SICK, Portion.PM, "today"),
        (("sick", "today", "afternoon"), AbsenceType.SICK, Portion.PM, "today"),
        (("toil", "12", "jun"), AbsenceType.FLEXI, Portion.FULL, "12 jun"),
        (
            ("annual", "monday", "to", "friday"),
            AbsenceType.ANNUAL,
            Portion.FULL,
            "monday to friday",
        ),
        (
            ("annual", "monday", "to", "friday", "am"),
            AbsenceType.ANNUAL,
            Portion.AM,
            "monday to friday",
        ),
        (("cancel", "friday"), None, Portion.FULL, "friday"),
        (("annual",), AbsenceType.ANNUAL, Portion.FULL, ""),
    ],
)
def test_the_grammar_splits_into_kind_portion_and_when(
    words: tuple[str, ...], kind: AbsenceType | None, portion: Portion, when: str
) -> None:
    """The kind comes back resolved. `None` is a cancellation, and only that."""
    assert parse_request(words) == Request(kind, portion, when)


def test_a_portion_is_only_taken_from_the_end() -> None:
    """So a month name or a note cannot be mistaken for one."""
    assert parse_request(("annual", "1", "may"))[1] is Portion.FULL


@pytest.mark.parametrize("word", ["someday", "vacation", "holidays"])
def test_an_unknown_kind_is_refused_by_name(word: str) -> None:
    import click

    with pytest.raises(click.UsageError, match=word):
        parse_request((word, "friday"))


# -- planning and confirming -------------------------------------------------


def test_a_dry_run_writes_nothing(services: Services, session: Session) -> None:
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


def test_a_half_day(services: Services, session: Session) -> None:
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


def test_a_backwards_range_is_refused_before_planning(services: Services) -> None:
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


def test_a_span_of_only_weekends_books_nothing_and_says_so(
    services: Services, session: Session
) -> None:
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


# -- what the confirmation says ----------------------------------------------


def test_the_render_names_the_bank_holiday(services: Services) -> None:
    plan = services.absence.plan(
        date(2026, 8, 28), date(2026, 9, 2), AbsenceType.ANNUAL
    )
    shown = render(plan)

    assert "Summer bank holiday" in shown
    assert "not a working day" in shown
    assert "3 days" in shown


def test_the_render_shows_the_allowance_moving(services: Services) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert "25 → 20 days left" in render(plan)


def test_annual_leave_does_not_warn_about_the_flexi_balance(
    services: Services,
) -> None:
    """It does not touch it, so a balance already in deficit is not news."""
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.ANNUAL, available_toil_days=-90.0
    )
    assert "deficit" not in render(plan)


def test_taking_toil_beyond_the_balance_warns(services: Services) -> None:
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=2.0
    )
    assert "deficit" in render(plan)


# -- cancelling --------------------------------------------------------------


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


def test_cancelling_nothing_says_so(services: Services) -> None:
    code = run(
        services,
        ("cancel", "friday"),
        note=None,
        assume_yes=True,
        dry_run=False,
        today=MONDAY,
    )
    assert code == 1


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


def test_saying_nothing_at_all_is_told_what_the_kinds_are() -> None:
    """`flexi leave` with the arguments quoted away, or a shell that ate them.

    The answer has to be the vocabulary, not "missing argument": the whole
    point of the grammar is that the first word comes from a closed list.
    """
    import click

    with pytest.raises(click.UsageError, match="annual, sick, toil, unpaid, other"):
        parse_request(())


# -- what the confirmation says ----------------------------------------------


def test_a_day_that_is_already_booked_is_shown_as_refused(
    services: Services, session: Session
) -> None:
    """A refusal is not a skip.

    A weekend is passed over because nobody meant it. A clash is a day
    somebody did mean and cannot have, and it carries the reason so the
    person can see which day to leave out of the second attempt.
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


# -- being asked before anything is written ----------------------------------


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
    """The plan is shown, the question is asked, and no is honoured.

    The default is no: this runs after a block of text somebody may have
    scrolled past, and a bare return must not book a week of leave.
    """
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


def test_declining_the_cancellation_leaves_the_leave_alone(
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
