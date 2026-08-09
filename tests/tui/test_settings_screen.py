"""Tests for Slice 11: settings screen.

Covers: settings save updates are reflected via SettingsService.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from flexi.services.settings import SettingsService


class TestSettingsRoundTrip:
    def test_save_and_read_back(self, session: Session) -> None:
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

    def test_update_settings_reflects_immediately(self, session: Session) -> None:
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

    def test_add_next_year_entitlement(self, session: Session) -> None:
        svc = SettingsService(session)
        svc.save_entitlement(2026, 25.0)
        svc.save_entitlement(2027, 25.0)

        ents = svc.all_entitlements()
        assert len(ents) == 2
        assert ents[0].year == 2026
        assert ents[1].year == 2027
