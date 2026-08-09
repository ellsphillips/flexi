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
def test_a_status_is_named_by_word_or_initial(
    status: str, expected: StatusOption
) -> None:
    """Both "arrive" and "a" reach the same option."""
    assert StatusOption.from_str(status) == expected
