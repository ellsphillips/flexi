"""Settings persistence, half-day entitlements, and which leave year is active."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import date, time, timedelta
from functools import partial
from unittest.mock import Mock

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from flexi.config import CONFIG
from flexi.constants import DEFAULT_DIVISION, AbsenceType, Division
from flexi.domain.punch import Window
from flexi.models.database.db import DEFAULT_WINDOW_END, DEFAULT_WINDOW_START
from flexi.models.database.engine import get_session
from flexi.services.settings import (
    INVALID_ENTITLEMENT,
    SettingsService,
    SettingsUpdate,
    duration_minutes,
    format_clock_time,
    format_window,
    parse_clock_time,
    parse_entitlement_days,
    parse_month_day,
    parse_settings,
    validate_window,
)


@pytest.fixture
def svc(session: Session) -> SettingsService:
    return SettingsService(session)


def _do_setup(svc: SettingsService) -> None:
    """Complete the minimal setup."""
    svc.save_settings(
        parse_settings(
            leave_year_start="01-01",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    # The active leave year, not a fixed one. A hardcoded 2026 here is compared
    # against the real clock by get_active_entitlement_days, so the test would
    # have started failing on 1 January 2027 with nothing having changed.
    svc.save_entitlement(svc.active_leave_year(), 25.0)


# ---------- setup-complete validation ----------


class TestSetupComplete:
    def test_incomplete_without_settings(self, svc: SettingsService) -> None:
        assert svc.is_setup_complete() is False

    def test_complete_after_save(self, svc: SettingsService) -> None:
        _do_setup(svc)
        assert svc.is_setup_complete() is True

    def test_incomplete_with_empty_field(self, svc: SettingsService) -> None:
        with pytest.raises(ValueError, match="Invalid date format"):
            svc.save_settings(
                parse_settings(
                    leave_year_start="",
                    working_days="0,1,2,3,4",
                    bank_holiday_division="england-and-wales",
                    auto_close_time="18:00",
                )
            )
        assert svc.is_setup_complete() is False


# ---------- settings persistence ----------


class TestSettingsPersistence:
    def test_save_and_retrieve(self, svc: SettingsService) -> None:
        _do_setup(svc)
        s = svc.get_settings()
        assert s is not None
        assert s.leave_year_start == "01-01"
        assert s.bank_holiday_division == "england-and-wales"

    def test_update_existing(self, svc: SettingsService) -> None:
        _do_setup(svc)
        svc.save_settings(
            parse_settings(
                leave_year_start="04-01",
                working_days="0,1,2,3",
                bank_holiday_division="scotland",
                auto_close_time="17:30",
            )
        )
        s = svc.get_settings()
        assert s is not None
        assert s.leave_year_start == "04-01"
        assert s.bank_holiday_division == "scotland"

    def test_survives_new_session(self, engine: Engine) -> None:
        s1 = get_session(engine)
        svc1 = SettingsService(s1)
        _do_setup(svc1)
        s1.close()

        s2 = get_session(engine)
        svc2 = SettingsService(s2)
        assert svc2.is_setup_complete() is True
        s2.close()


# ---------- helpers ----------


class TestHelpers:
    def test_working_day_indices(self, svc: SettingsService) -> None:
        _do_setup(svc)
        assert svc.get_working_day_indices() == [0, 1, 2, 3, 4]

    def test_auto_close_time(self, svc: SettingsService) -> None:
        svc.save_settings(
            parse_settings(
                leave_year_start="01-01",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time="17:30",
            )
        )
        assert svc.get_auto_close_time() == time(17, 30)


# ---------- leave entitlements ----------


class TestLeaveEntitlements:
    def test_they_are_listed_in_year_order(self, svc: SettingsService) -> None:
        """`_add_next_year` takes `ents[-1]`, so the order is load-bearing.

        Moved here from `tests/tui/test_settings_screen.py`, which held three
        service round-trips under a name that promised a screen test.
        """
        svc.save_entitlement(2027, 25.0)
        svc.save_entitlement(2026, 22.0)

        years = [row.year for row in svc.all_entitlements()]

        assert years == [2026, 2027]

    def test_half_day_support(self, svc: SettingsService) -> None:
        ent = svc.save_entitlement(2026, 25.5)
        assert ent.days == 25.5

    def test_update_existing_year(self, svc: SettingsService) -> None:
        svc.save_entitlement(2026, 25.0)
        svc.save_entitlement(2026, 30.0)
        ent = svc.get_entitlement(2026)
        assert ent is not None
        assert ent.days == 30.0

    @pytest.mark.parametrize(
        "days",
        [
            pytest.param(-1.0, id="negative"),
            pytest.param(float("nan"), id="not-a-number"),
            pytest.param(float("inf"), id="positive-infinity"),
            pytest.param(float("-inf"), id="negative-infinity"),
        ],
    )
    def test_invalid_allowances_are_rejected_before_persistence(
        self, svc: SettingsService, days: float
    ) -> None:
        with pytest.raises(ValueError, match="finite and zero or more"):
            svc.save_entitlement(2026, days)

        assert svc.get_entitlement(2026) is None

    @pytest.mark.parametrize("raw", ["-1", "nan", "inf", "twenty five"])
    def test_user_entered_allowances_share_one_error_contract(self, raw: str) -> None:
        with pytest.raises(ValueError, match="Entitlement must be a number") as raised:
            parse_entitlement_days(raw)

        assert str(raised.value) == INVALID_ENTITLEMENT

    def test_active_entitlement(self, svc: SettingsService) -> None:
        _do_setup(svc)
        assert svc.get_active_entitlement_days() == 25.0

    def test_no_entitlement_returns_none(self, svc: SettingsService) -> None:
        _do_setup(svc)
        assert svc.get_active_entitlement_days(date(2020, 1, 1)) is None


# ---------- active leave year ----------


class TestActiveLeaveYear:
    def test_jan_start(self, svc: SettingsService) -> None:
        svc.save_settings(
            parse_settings(
                leave_year_start="01-01",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time="18:00",
            )
        )
        assert svc.active_leave_year(date(2026, 6, 1)) == 2026
        assert svc.active_leave_year(date(2026, 1, 1)) == 2026

    def test_apr_start(self, svc: SettingsService) -> None:
        svc.save_settings(
            parse_settings(
                leave_year_start="04-01",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time="18:00",
            )
        )
        assert svc.active_leave_year(date(2026, 6, 1)) == 2026
        assert svc.active_leave_year(date(2026, 3, 1)) == 2025


# ---------- parse_month_day ----------


class TestParseMonthDay:
    def test_dash_separator(self) -> None:
        assert parse_month_day("04-01") == (4, 1)

    def test_slash_separator(self) -> None:
        assert parse_month_day("10/20") == (10, 20)

    def test_single_digits(self) -> None:
        assert parse_month_day("1-1") == (1, 1)

    def test_normalised_on_save(self, svc: SettingsService) -> None:
        svc.save_settings(
            parse_settings(
                leave_year_start="10/20",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time="18:00",
            )
        )
        s = svc.get_settings()
        assert s is not None
        assert s.leave_year_start == "10-20"

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid date format"):
            parse_month_day("not-a-date")

    def test_month_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="Month"):
            parse_month_day("13-01")

    def test_day_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="Day"):
            parse_month_day("01-32")

    @pytest.mark.parametrize("typed", ["02-30", "04-31", "11-31"])
    def test_impossible_calendar_day(self, typed: str) -> None:
        with pytest.raises(ValueError, match="not valid for month"):
            parse_month_day(typed)

    def test_leap_day_is_a_valid_leave_year_boundary(self) -> None:
        assert parse_month_day("02-29") == (2, 29)


# ---- auto-close time ----


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("18:00", "18:00"),
        ("6pm", "18:00"),
        ("9.30am", "09:30"),
        ("18", "18:00"),
        ("12am", "00:00"),
        ("8:5", "08:05"),
    ],
)
def test_the_auto_close_time_is_normalised_on_the_way_in(
    svc: SettingsService, typed: str, stored: str
) -> None:
    """A field labelled "auto-close time" invites `6pm` as readily as `18:00`."""
    svc.save_settings(
        parse_settings(
            leave_year_start="04-06",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time=typed,
        )
    )
    settings = svc.get_settings()
    assert settings is not None
    assert settings.auto_close_time == stored


@pytest.mark.parametrize("typed", ["half six", "25:00", "18:99", "", "6 o clock"])
def test_a_time_that_cannot_be_read_is_refused_rather_than_stored(
    svc: SettingsService, typed: str
) -> None:
    """It used to be stored unchecked, and then nothing would open.

    Saving succeeded, `is_initialised()` said yes, and every command after it
    died unpacking the value -- from `open_database` on the CLI and from
    `App.on_mount` before a screen was drawn. The only way out was Start again,
    which erases the records.

    Both screens already wrap `save_settings` in `except ValueError: notify`, so
    refusing here is what puts the message in front of somebody.
    """
    with pytest.raises(ValueError, match="time|range"):
        svc.save_settings(
            parse_settings(
                leave_year_start="04-06",
                working_days="Mon-Fri",
                bank_holiday_division="england-and-wales",
                auto_close_time=typed,
            )
        )


@pytest.mark.parametrize("stored", ["6pm", "half six", "25:00", ""])
def test_a_stored_time_that_cannot_be_read_falls_back(
    svc: SettingsService, session: Session, stored: str
) -> None:
    """Databases written before the validation still exist, and must open.

    Raising on read is not a settings error, it is an application that will not
    start -- with no way in to correct the setting.

    `6pm` is here because it is what the original bug wrote; the other three are
    here because `6pm` is *readable*, so on its own this test asserted the
    parser and never once reached the fallback it is named after.
    """
    _do_setup(svc)
    session.execute(
        text("UPDATE settings SET auto_close_time = :stored"), {"stored": stored}
    )
    session.commit()

    assert svc.get_auto_close_time() == time(18, 0)


def test_a_time_with_no_settings_row_at_all_falls_back(svc: SettingsService) -> None:
    """`App.on_mount` reads this before setup has been offered."""
    assert svc.get_auto_close_time() == time(18, 0)


@pytest.mark.parametrize("typed", ["13pm", "0am", "24pm"])
def test_an_hour_that_cannot_take_a_meridiem_is_refused(typed: str) -> None:
    """`13pm` is a typo, and reading it as 1am or 1pm is a guess.

    Guessing puts the auto-close an hour or twelve from where somebody meant it,
    and they find out when a day closes at the wrong time weeks later.
    """
    with pytest.raises(ValueError, match="does not take am or pm"):
        parse_clock_time(typed)


# ---- reading settings that are not there ----


def test_the_day_window_falls_back_before_setup(svc: SettingsService) -> None:
    """The punch strip is drawn on the splash screen, before there is a row."""
    assert svc.get_day_window() == Window.parse(
        DEFAULT_WINDOW_START, DEFAULT_WINDOW_END
    )


def test_the_working_week_falls_back_before_setup(svc: SettingsService) -> None:
    """Monday to Friday, rather than a week with no working days in it.

    An empty list makes every day a non-working day, which would draw a calendar
    of weekends and refuse every booking on it.
    """
    assert svc.get_working_day_indices() == [0, 1, 2, 3, 4]


def test_a_stored_working_week_that_cannot_be_read_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """A settings problem is not a reason to refuse to open somebody's records."""
    _do_setup(svc)
    session.execute(text("UPDATE settings SET working_days = 'weekdays'"))
    session.commit()

    assert svc.get_working_day_indices() == [0, 1, 2, 3, 4]


def test_a_leave_year_start_that_cannot_be_read_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """It raised, where the four accessors beside it fall back.

    A settings problem is not a reason to refuse to open somebody's records --
    there would be no way in to correct the setting.
    """
    _do_setup(svc)
    session.execute(text("UPDATE settings SET leave_year_start = 'April the 1st'"))
    session.commit()

    assert svc.get_leave_year_start() == (1, 1)


def test_a_day_window_that_cannot_be_read_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """It handed its strings straight on to `Window.parse`, which raises.

    Inside a widget's `render`, where Textual logs the traceback and swallows
    it — so the symptom is a blank panel and no message. `save_settings`
    normalises the leave year and the auto-close time and does not normalise
    these two, so an unreadable pair is reachable.
    """
    _do_setup(svc)
    session.execute(text("UPDATE settings SET day_window_start = 'sunrise'"))
    session.commit()

    assert svc.get_day_window() == Window.parse(
        DEFAULT_WINDOW_START, DEFAULT_WINDOW_END
    )


def test_the_division_falls_back_before_setup(svc: SettingsService) -> None:
    """Something has to be asked of GOV.UK before anybody has chosen a region."""
    assert svc.get_division() is DEFAULT_DIVISION


def test_a_stored_division_this_build_does_not_know_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """A region GOV.UK has stopped publishing must not close the application.

    The column is a free-text slug, and the three members are what this build
    understands. Raising here would refuse to open the records of anybody whose
    row was written by a version that knew a fourth.
    """
    _do_setup(svc)
    session.execute(text("UPDATE settings SET bank_holiday_division = 'mercia'"))
    session.commit()

    assert svc.get_division() is DEFAULT_DIVISION


# ---- the optional fields ----


def test_the_optional_fields_keep_their_values_when_they_are_not_passed(
    svc: SettingsService,
) -> None:
    """The setup screen writes four fields; the settings screen writes seven.

    Every save from the setup screen would otherwise reset a contracted day and
    a punch-strip window that somebody had already changed, because those three
    arrive as `None` from that call site.
    """
    svc.save_settings(
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="scotland",
            auto_close_time="18:00",
            contracted_minutes=420,
            day_window_start="08:00",
            day_window_end="19:00",
        )
    )

    svc.save_settings(
        parse_settings(
            leave_year_start="04-06",
            working_days="Mon-Thu",
            bank_holiday_division="scotland",
            auto_close_time="17:00",
        )
    )

    assert svc.get_contracted() == timedelta(minutes=420)
    assert svc.get_day_window() == Window.parse("08:00", "19:00")


def test_the_optional_fields_are_updated_when_they_are_passed(
    svc: SettingsService,
) -> None:
    """A shorter contracted day is what a part-time week is made of."""
    _do_setup(svc)

    svc.save_settings(
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
            contracted_minutes=222,
            day_window_start="07:30",
            day_window_end="20:30",
        )
    )

    assert svc.get_contracted() == timedelta(minutes=222)
    assert svc.get_day_window() == Window.parse("07:30", "20:30")


def test_raw_settings_are_parsed_into_an_immutable_domain_value() -> None:
    update = parse_settings(
        leave_year_start="4/1",
        working_days="Fri, Mon, Mon",
        bank_holiday_division="scotland",
        auto_close_time="6pm",
        contracted_minutes=0,
        day_window_start="8am",
        day_window_end="7pm",
    )

    assert update == SettingsUpdate(
        leave_year_start=(4, 1),
        working_days=(0, 4),
        division=Division.SCOTLAND,
        auto_close=time(18),
        contracted=timedelta(0),
        day_window=Window(time(8), time(19)),
    )
    assert not hasattr(update, "__dict__")
    field = "working_days"
    with pytest.raises(FrozenInstanceError):
        setattr(update, field, (0,))


def test_zero_contracted_time_is_an_explicit_value(svc: SettingsService) -> None:
    svc.save_settings(
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
            contracted_minutes=0,
        )
    )

    row = svc.get_settings()
    assert row is not None
    assert row.contracted_minutes == 0
    assert svc.get_contracted() == timedelta(0)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (timedelta(microseconds=-1), "cannot be negative"),
        (timedelta(seconds=30), "whole minutes"),
    ],
)
def test_contracted_time_never_loses_precision(value: timedelta, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        duration_minutes(value)

    assert duration_minutes(timedelta(0)) == 0


@pytest.mark.parametrize("value", [time(18, 0, 1), time(18, 0, 0, 1)])
def test_clock_time_never_loses_precision(value: time) -> None:
    with pytest.raises(ValueError, match="whole minutes"):
        format_clock_time(value)


@pytest.mark.parametrize(
    "window",
    [Window(time(19), time(8)), Window(time(8), time(8))],
)
def test_a_day_window_must_move_forwards(window: Window) -> None:
    with pytest.raises(ValueError, match="after its start"):
        validate_window(window)


def test_a_valid_window_formats_without_losing_its_type() -> None:
    window = Window(time(8), time(19))

    assert validate_window(window) is window
    assert format_window(window) == ("08:00", "19:00")


def test_a_partial_raw_window_is_refused() -> None:
    with pytest.raises(ValueError, match="provided together"):
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
            day_window_start="08:00",
        )


def test_an_unknown_raw_division_is_refused() -> None:
    with pytest.raises(ValueError, match="not a valid Division"):
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="mercia",
            auto_close_time="18:00",
        )


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (
            SettingsUpdate(
                leave_year_start=(1, 1),
                working_days=(),
                division=Division.SCOTLAND,
                auto_close=time(18),
            ),
            "working day",
        ),
        (
            SettingsUpdate(
                leave_year_start=(1, 1),
                working_days=(7,),
                division=Division.SCOTLAND,
                auto_close=time(18),
            ),
            "out of range",
        ),
        (
            SettingsUpdate(
                leave_year_start=(1, 1),
                working_days=(0,),
                division=Division.SCOTLAND,
                auto_close=time(18, 0, 1),
            ),
            "whole minutes",
        ),
        (
            SettingsUpdate(
                leave_year_start=(1, 1),
                working_days=(0,),
                division=Division.SCOTLAND,
                auto_close=time(18),
                day_window=Window(time(19), time(8)),
            ),
            "after its start",
        ),
    ],
)
def test_directly_constructed_updates_are_validated_before_writing(
    svc: SettingsService, update: SettingsUpdate, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        svc.save_settings(update)

    assert svc.get_settings() is None


def test_settings_and_entitlements_commit_once(
    svc: SettingsService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = Mock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", commit)
    update = parse_settings(
        leave_year_start="01-01",
        working_days="Mon-Fri",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )

    svc.save_settings_and_entitlements(update, {2026: 25.0, 2027: 26.0})

    commit.assert_called_once_with()
    assert [row.year for row in svc.all_entitlements()] == [2026, 2027]


@pytest.mark.parametrize("operation", ["settings", "entitlement", "combined"])
def test_failed_commits_are_rolled_back(
    svc: SettingsService,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    failure = SQLAlchemyError("write failed")
    rollback = Mock(wraps=session.rollback)
    monkeypatch.setattr(session, "commit", Mock(side_effect=failure))
    monkeypatch.setattr(session, "rollback", rollback)
    update = parse_settings(
        leave_year_start="01-01",
        working_days="Mon-Fri",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )

    attempt: Callable[[], object]
    if operation == "settings":
        attempt = partial(svc.save_settings, update)
    elif operation == "entitlement":
        attempt = partial(svc.save_entitlement, 2026, 25.0)
    else:
        attempt = partial(svc.save_settings_and_entitlements, update, {2026: 25.0})

    with pytest.raises(SQLAlchemyError, match="write failed"):
        attempt()

    rollback.assert_called_once_with()


# ---- the closed vocabularies ----


def test_every_absence_type_declares_its_details() -> None:
    """Adding a member and forgetting the table used to be a KeyError.

    On the booking path, with mypy clean and the suite green. The guard is at
    import time, so this test is really asserting that the guard is still there
    and still reachable.
    """
    for kind in AbsenceType:
        assert kind.label, kind.name
        assert kind.short, kind.name
        assert kind.token, kind.name


def test_every_absence_type_has_a_key_that_books_it() -> None:
    """The year calendar's legend derives from this rather than restating it.

    It used to hardcode `[("A", "annual"), ("S", "sick"), ("T", "toil")]` while
    `CONFIG.hotkeys` owned those keys, so rebinding one made the legend lie.
    """
    keys = {kind: CONFIG.hotkeys.book(kind) for kind in AbsenceType}

    assert all(keys.values()), keys
    assert len(set(keys.values())) == len(keys), f"two types share a key: {keys}"


def test_every_division_has_a_label() -> None:
    assert [value for _, value in Division.choices()] == [d.value for d in Division]
    assert DEFAULT_DIVISION in set(Division)
