"""Tests for Slice 11: settings screen.

Covers: settings save updates are reflected via SettingsService.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.services.settings import SettingsService


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


class TestSettingsRoundTrip:
    def test_save_and_read_back(self, session) -> None:
        svc = SettingsService(session)
        svc.save_settings(
            leave_year_start="04-01",
            working_days="0,1,2,3,4",
            bank_holiday_division="scotland",
            auto_close_time="17:30",
        )
        svc.save_entitlement(2026, 28.5)

        s = svc.get_settings()
        assert s is not None
        assert s.leave_year_start == "04-01"
        assert s.bank_holiday_division == "scotland"

        ent = svc.get_entitlement(2026)
        assert ent is not None
        assert ent.days == 28.5

    def test_update_settings_reflects_immediately(self, session) -> None:
        svc = SettingsService(session)
        svc.save_settings(
            leave_year_start="01-01",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
        svc.save_settings(
            leave_year_start="04-01",
            working_days="0,1,2,3",
            bank_holiday_division="northern-ireland",
            auto_close_time="17:00",
        )

        s = svc.get_settings()
        assert s is not None
        assert s.leave_year_start == "04-01"
        assert s.working_days == "0,1,2,3"
        assert s.bank_holiday_division == "northern-ireland"

    def test_add_next_year_entitlement(self, session) -> None:
        svc = SettingsService(session)
        svc.save_entitlement(2026, 25.0)
        svc.save_entitlement(2027, 25.0)

        ents = svc.all_entitlements()
        assert len(ents) == 2
        assert ents[0].year == 2026
        assert ents[1].year == 2027
