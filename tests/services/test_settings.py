"""Tests for Slice 2: settings service.

Covers: setup-complete validation, settings persistence, leave entitlement
half-day support, active leave year calculation.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.services.settings import SettingsService, parse_month_day


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(tmp_path / "test.db")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    s = get_session(engine)
    yield s
    s.close()


@pytest.fixture
def svc(session) -> SettingsService:
    return SettingsService(session)


def _do_setup(svc: SettingsService) -> None:
    """Complete the minimal setup."""
    svc.save_settings(
        leave_year_start="01-01",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    svc.save_entitlement(2026, 25.0)


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

    def test_survives_new_session(self, engine) -> None:
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
