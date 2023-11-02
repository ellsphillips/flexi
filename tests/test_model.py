import datetime

from flexi.log.model import Log
from flexi.types import Leave


def test_leave_clears_all_sessions(model: Log) -> None:
    """Check that leave clears all sessions."""
    model.days[0].leave = Leave.ANNUAL
    assert len(model.days[0].sessions) == 0


def test_new_day_creation(model: Log) -> None:
    """Check that a new day is created."""
    date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    tomorrow = model[date]

    assert tomorrow.date == date
    assert tomorrow.date in [d.date for d in model.days]
