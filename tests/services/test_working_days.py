"""Whatever is typed into "working days", the application still opens."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from flexi.services.settings import SettingsService, parse_settings, parse_working_days


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("0,1,2,3,4", [0, 1, 2, 3, 4]),
        ("Mon-Fri", [0, 1, 2, 3, 4]),
        ("mon,tue,wed,thu,fri", [0, 1, 2, 3, 4]),
        ("Monday, Wednesday, Friday", [0, 2, 4]),
        ("0-4", [0, 1, 2, 3, 4]),
        ("tue, thu", [1, 3]),
        ("sat,sun", [5, 6]),
        ("  MON , fri  ", [0, 4]),
        ("mon,mon,tue", [0, 1]),
    ],
)
def test_it_reads_what_a_person_would_type(typed: str, expected: list[int]) -> None:
    assert parse_working_days(typed) == expected


@pytest.mark.parametrize(
    "typed", ["", "   ", "xyz", "9", "-1", "fri-mon", "mon-xyz", ","]
)
def test_it_refuses_what_it_cannot_read(typed: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        parse_working_days(typed)


def test_refusal_says_what_to_do_instead() -> None:
    with pytest.raises(ValueError, match="Monday") as raised:
        parse_working_days("someday")
    assert "someday" in str(raised.value)


def test_saving_normalises_whatever_was_typed(session: Session) -> None:
    settings = SettingsService(session)
    settings.save_settings(
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    stored = settings.get_settings()
    assert stored is not None
    assert stored.working_days == "0,1,2,3,4"
    assert settings.get_working_day_indices() == [0, 1, 2, 3, 4]


def test_saving_something_unreadable_is_refused(session: Session) -> None:
    settings = SettingsService(session)
    with pytest.raises(ValueError, match="not a day"):
        settings.save_settings(
            parse_settings(
                leave_year_start="01-01",
                working_days="whenever I feel like it",
                bank_holiday_division="england-and-wales",
                auto_close_time="18:00",
            )
        )
    assert settings.get_settings() is None


def test_unreadable_stored_value_does_not_stop_the_app(session: Session) -> None:
    """A database written before the field was validated still opens."""
    settings = SettingsService(session)
    settings.save_settings(
        parse_settings(
            leave_year_start="01-01",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    stored = settings.get_settings()
    assert stored is not None
    stored.working_days = "Mon-Fri"  # as an unvalidated Flexi wrote it
    session.commit()

    assert settings.get_working_day_indices() == [0, 1, 2, 3, 4]
