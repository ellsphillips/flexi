import pytest

from flexi.constants import StatusOption


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("arrive", StatusOption.ARRIVE),
        ("depart", StatusOption.DEPART),
        ("a", StatusOption.ARRIVE),
        ("d", StatusOption.DEPART),
    ],
)
def test_StatusOption(status: str, expected: StatusOption) -> None:
    """It StatusOptions in or out."""
    assert StatusOption.from_str(status) == expected
