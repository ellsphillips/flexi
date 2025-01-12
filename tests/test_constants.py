import pytest

from flexi.constants import Clock


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("in", Clock.IN),
        ("out", Clock.OUT),
        ("i", Clock.IN),
        ("o", Clock.OUT),
    ],
)
def test_clock(clock: str, expected: Clock) -> None:
    """It clocks in or out."""
    assert Clock.from_str(clock) == expected
