import datetime

from flexi.log.model import Log, Session
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
    """Check that a session duration is calculated correctly."""
    session = Session(clock_in="09:00", clock_out="12:30")
    assert session.duration == datetime.timedelta(hours=3, minutes=30)
