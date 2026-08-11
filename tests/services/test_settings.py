"""Settings persistence, half-day entitlements, and which leave year is active."""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from flexi.config import CONFIG
from flexi.constants import DEFAULT_DIVISION, AbsenceType, Division
from flexi.models.database.app import get_session
from flexi.services.settings import SettingsService, parse_month_day


@pytest.fixture
def svc(session: Session) -> SettingsService:
    return SettingsService(session)


def _do_setup(svc: SettingsService) -> None:
    """Complete the minimal setup."""
    svc.save_settings(
        leave_year_start="01-01",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
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
                leave_year_start="",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time="18:00",
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
            leave_year_start="04-01",
            working_days="0,1,2,3",
            bank_holiday_division="scotland",
            auto_close_time="17:30",
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

    def test_is_working_day(self, svc: SettingsService) -> None:
        _do_setup(svc)
        assert svc.is_working_day(0) is True  # Monday
        assert svc.is_working_day(5) is False  # Saturday

    def test_auto_close_time(self, svc: SettingsService) -> None:
        svc.save_settings(
            leave_year_start="01-01",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="17:30",
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
            leave_year_start="01-01",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
        assert svc.active_leave_year(date(2026, 6, 1)) == 2026
        assert svc.active_leave_year(date(2026, 1, 1)) == 2026

    def test_apr_start(self, svc: SettingsService) -> None:
        svc.save_settings(
            leave_year_start="04-01",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
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
            leave_year_start="10/20",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
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
        leave_year_start="04-06",
        working_days="Mon-Fri",
        bank_holiday_division="england-and-wales",
        auto_close_time=typed,
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
    died unpacking the value -- from `_open_database` on the CLI and from
    `App.on_mount` before a screen was drawn. The only way out was Start again,
    which erases the records.

    Both screens already wrap `save_settings` in `except ValueError: notify`, so
    refusing here is what puts the message in front of somebody.
    """
    with pytest.raises(ValueError, match="time|range"):
        svc.save_settings(
            leave_year_start="04-06",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time=typed,
        )


def test_a_stored_time_that_cannot_be_read_falls_back(
    svc: SettingsService, session: Session
) -> None:
    """Databases written before the validation still exist, and must open.

    Raising on read is not a settings error, it is an application that will not
    start -- with no way in to correct the setting.
    """
    _do_setup(svc)
    session.execute(text("UPDATE settings SET auto_close_time = '6pm'"))
    session.commit()

    assert svc.get_auto_close_time() == time(18, 0)


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
