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
from flexi.services.setup import REQUIRED_SETTINGS


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
    # The active leave year, not a fixed one: `get_active_entitlement_days`
    # compares it against the clock.
    svc.save_entitlement(svc.active_leave_year(), 25.0)


# ---------- setup-complete validation ----------


class TestSetupComplete:
    def test_incomplete_without_settings(self, svc: SettingsService) -> None:
        assert svc.is_setup_complete() is False

    def test_complete_after_save(self, svc: SettingsService) -> None:
        _do_setup(svc)
        assert svc.is_setup_complete() is True

    @pytest.mark.parametrize("field", REQUIRED_SETTINGS)
    def test_every_required_field_gates_setup(
        self, svc: SettingsService, session: Session, field: str
    ) -> None:
        """One list of required settings, read by both gates that ask.

        `flexi clock in` reads `REQUIRED_SETTINGS` over a read-only connection
        and bare `flexi` asks this.
        """
        _do_setup(svc)
        stored = svc.get_settings()
        assert stored is not None

        setattr(stored, field, "")
        session.commit()

        assert svc.is_setup_complete() is False

    def test_new_required_setting_gates_setup(
        self, svc: SettingsService, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adding to the list is the whole change; nothing here has to be edited."""
        _do_setup(svc)
        monkeypatch.setattr(
            "flexi.services.settings.REQUIRED_SETTINGS",
            (*REQUIRED_SETTINGS, "tracking_since"),
        )
        stored = svc.get_settings()
        assert stored is not None

        stored.tracking_since = None
        session.commit()

        assert svc.is_setup_complete() is False

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
        """`_add_next_year` takes `ents[-1]`, so the order is load-bearing."""
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
    def test_invalid_allowances_are_not_stored(
        self, svc: SettingsService, days: float
    ) -> None:
        with pytest.raises(ValueError, match="finite and zero or more"):
            svc.save_entitlement(2026, days)

        assert svc.get_entitlement(2026) is None

    @pytest.mark.parametrize("raw", ["-1", "nan", "inf", "twenty five"])
    def test_typed_allowances_share_one_error(self, raw: str) -> None:
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

    def test_leap_day_is_a_valid_year_start(self) -> None:
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
def test_auto_close_time_is_normalised(
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
def test_unreadable_time_is_refused_on_save(svc: SettingsService, typed: str) -> None:
    """Both screens wrap `save_settings` in `except ValueError: notify`."""
    with pytest.raises(ValueError, match=r"time|range"):
        svc.save_settings(
            parse_settings(
                leave_year_start="04-06",
                working_days="Mon-Fri",
                bank_holiday_division="england-and-wales",
                auto_close_time=typed,
            )
        )


@pytest.mark.parametrize("stored", ["6pm", "half six", "25:00", ""])
def test_unreadable_stored_time_falls_back(
    svc: SettingsService, session: Session, stored: str
) -> None:
    """Databases written before the validation still exist, and must open.

    `6pm` parses, so the unreadable three are what reach the fallback.
    """
    _do_setup(svc)
    session.execute(
        text("UPDATE settings SET auto_close_time = :stored"), {"stored": stored}
    )
    session.commit()

    assert svc.get_auto_close_time() == time(18, 0)


def test_auto_close_falls_back_with_no_row(svc: SettingsService) -> None:
    """`App.on_mount` reads this before setup has been offered."""
    assert svc.get_auto_close_time() == time(18, 0)


@pytest.mark.parametrize("typed", ["13pm", "0am", "24pm"])
def test_impossible_meridiem_hour_is_refused(typed: str) -> None:
    """`13pm` is a typo, and reading it as 1am or 1pm is a guess."""
    with pytest.raises(ValueError, match="does not take am or pm"):
        parse_clock_time(typed)


# ---- reading settings that are not there ----


def test_day_window_falls_back_before_setup(svc: SettingsService) -> None:
    """The punch strip is drawn on the splash screen, before there is a row."""
    assert svc.get_day_window() == Window.parse(
        DEFAULT_WINDOW_START, DEFAULT_WINDOW_END
    )


def test_working_week_falls_back_before_setup(svc: SettingsService) -> None:
    """An empty week makes every day a non-working day and refuses every booking."""
    assert svc.get_working_day_indices() == [0, 1, 2, 3, 4]


def test_unreadable_working_week_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """A settings problem is not a reason to refuse to open the records."""
    _do_setup(svc)
    session.execute(text("UPDATE settings SET working_days = 'weekdays'"))
    session.commit()

    assert svc.get_working_day_indices() == [0, 1, 2, 3, 4]


def test_unreadable_leave_year_start_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """Raising would leave no way in to correct the setting."""
    _do_setup(svc)
    session.execute(text("UPDATE settings SET leave_year_start = 'April the 1st'"))
    session.commit()

    assert svc.get_leave_year_start() == (1, 1)


def test_unreadable_day_window_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """`Window.parse` raises, and this is read inside a widget's `render`.

    `save_settings` normalises the leave year and the auto-close time but not
    these two, so an unreadable pair is reachable.
    """
    _do_setup(svc)
    session.execute(text("UPDATE settings SET day_window_start = 'sunrise'"))
    session.commit()

    assert svc.get_day_window() == Window.parse(
        DEFAULT_WINDOW_START, DEFAULT_WINDOW_END
    )


def test_division_falls_back_before_setup(svc: SettingsService) -> None:
    """Something has to be asked of GOV.UK before a region has been chosen."""
    assert svc.get_division() is DEFAULT_DIVISION


def test_unknown_stored_division_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """The column is a free-text slug, so it may name a region this build lacks."""
    _do_setup(svc)
    session.execute(text("UPDATE settings SET bank_holiday_division = 'mercia'"))
    session.commit()

    assert svc.get_division() is DEFAULT_DIVISION


# ---- the optional fields ----


def test_omitted_optional_fields_keep_their_values(
    svc: SettingsService,
) -> None:
    """The setup screen writes four fields; the settings screen writes seven."""
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


def test_passed_optional_fields_are_updated(
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


def test_parse_settings_returns_a_frozen_value() -> None:
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
def test_day_window_must_move_forwards(window: Window) -> None:
    with pytest.raises(ValueError, match="after its start"):
        validate_window(window)


def test_valid_window_is_returned_and_formatted() -> None:
    window = Window(time(8), time(19))

    assert validate_window(window) is window
    assert format_window(window) == ("08:00", "19:00")


def test_partial_raw_window_is_refused() -> None:
    with pytest.raises(ValueError, match="provided together"):
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
            day_window_start="08:00",
        )


def test_unknown_raw_division_is_refused() -> None:
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
def test_constructed_updates_are_validated(
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


def test_every_absence_type_has_a_booking_key() -> None:
    """The year calendar's legend derives from `CONFIG.hotkeys`."""
    keys = {kind: CONFIG.hotkeys.book(kind) for kind in AbsenceType}

    assert all(keys.values()), keys
    assert len(set(keys.values())) == len(keys), f"two types share a key: {keys}"


def test_every_division_has_a_label() -> None:
    assert [value for _, value in Division.choices()] == [d.value for d in Division]
    assert DEFAULT_DIVISION in set(Division)
