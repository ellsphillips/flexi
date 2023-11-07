import datetime

from flexi.log.model import Log, Session, format_clock_time
from flexi.types import Leave


def test_leave_clears_all_sessions(log: Log) -> None:
    """Check that leave clears all sessions."""
    log.days[0].leave = Leave.ANNUAL
    assert len(log.days[0].sessions) == 0


def test_new_day_creation(log: Log) -> None:
    """Check that a new day is created."""
    date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    tomorrow = log[date]

    assert tomorrow.date == date
    assert tomorrow.date in [d.date for d in log.days]


def test_session_duration() -> None:
    """Check that session duration is correct."""
    session = Session(clock_in="09:00", clock_out="12:30")
    assert session.duration == datetime.timedelta(hours=3, minutes=30)


def test_session_duration_with_missing_clock_out() -> None:
    """Check that session duration is correct."""
    session = Session(clock_in="09:00", clock_out="")
    assert session.duration == datetime.timedelta()


def test_format_clock_time() -> None:
    """Check that clock time is formatted correctly."""
    dt = format_clock_time(
        time="09:00",
        day=datetime.date(2030, 1, 1),
    )
    assert dt == datetime.datetime(2030, 1, 1, 9, 0)  # noqa: DTZ001
