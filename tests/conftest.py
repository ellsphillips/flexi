import pytest

from flexi.log.model import Correction, Day, Log, Session
from flexi.types import Leave


def pytest_configure(config: pytest.Config) -> None:
    """Pytest configuration hook."""
    config.addinivalue_line("markers", "e2e: mark as end-to-end test.")


TEST_LOG = Log(
    days=[
        Day(
            date="2030-10-30",
            sessions=[
                Session(clock_in="09:00", clock_out="12:30"),
                Session(clock_in="13:00", clock_out="17:00"),
            ],
        ),
        Day(
            date="2030-10-31",
            sessions=[
                Session(clock_in="07:30", clock_out="09:00"),
                Session(clock_in="11:00", clock_out="13:00"),
                Session(clock_in="13:30", clock_out="16:00"),
            ],
        ),
        Day(
            date="2030-11-01",
            sessions=[
                Session(clock_in="13:00", clock_out="17:00"),
            ],
            corrections=[
                Correction(
                    message="Forgot to clock in this morning",
                    session=Session(clock_in="09:00", clock_out="12:30"),
                )
            ],
        ),
        Day(
            date="2030-11-02",
            leave=Leave.ANNUAL,
        ),
    ]
)


@pytest.fixture(scope="session", autouse=True)
def log() -> Log:
    """Fixture for the model."""
    return TEST_LOG
