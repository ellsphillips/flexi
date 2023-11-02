import datetime
from unittest.mock import Mock, patch

import pytest

from flexi.log.model import Day, Log, Session
from flexi.log.registrar import (
    arrive,
    currently_clocked_in,
    currently_clocked_out,
    depart,
)


@pytest.mark.parametrize(
    ("clock_in", "clock_out", "expected"),
    [
        ("", "", False),
        ("09:00", "17:00", False),
        ("09:00", "", True),
    ],
)
def test_if_currently_clocked_in(
    clock_in: str,
    clock_out: str,
    expected: bool,  # noqa: FBT001
) -> None:
    """Check if the user is able to clock in."""
    day = Day(
        date="2021-01-01",
        sessions=[Session(clock_in=clock_in, clock_out=clock_out)] if clock_in else [],
    )
    assert currently_clocked_in(day) is expected


@pytest.mark.parametrize(
    ("clock_in", "clock_out", "expected"),
    [
        ("", "", True),
        ("09:00", "17:00", True),
        ("09:00", "", False),
    ],
)
def test_if_currently_clocked_out(
    clock_in: str,
    clock_out: str,
    expected: bool,  # noqa: FBT001
) -> None:
    """Check if the user is able to clock out."""
    day = Day(
        date="2021-01-01",
        sessions=[Session(clock_in=clock_in, clock_out=clock_out)] if clock_in else [],
    )
    assert currently_clocked_out(day) is expected


@patch("flexi.log.registrar.currently_clocked_in")
def test_arrive(
    mocked_clock_status: Mock,
    capsys: pytest.CaptureFixture[str],
    model: Log,
) -> None:
    """Test arriving at work."""
    mocked_clock_status.return_value = True
    arrive(model)

    assert "You are already clocked in." in capsys.readouterr().out


def test_depart(
    capsys: pytest.CaptureFixture[str],
    model: Log,
) -> None:
    """Test departing work."""
    model[datetime.date.today().isoformat()].sessions = [
        Session(clock_in="09:00", clock_out="")
    ]
    depart(model)

    assert "Successfully clocked  OUT " in capsys.readouterr().out


@patch("flexi.log.registrar.currently_clocked_out")
def test_depart_if_clocked_out(
    mocked_clock_status: Mock,
    capsys: pytest.CaptureFixture[str],
    model: Log,
) -> None:
    """Test departing work."""
    mocked_clock_status.return_value = True
    depart(model)

    assert "No active session found. Please clock in first." in capsys.readouterr().out
