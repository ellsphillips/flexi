import pytest

from flexi.constants import AbsenceType, StatusOption


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


def test_every_absence_type_reads_inside_a_sentence() -> None:
    """`label.lower()` turned "TOIL" into "toil" in six places at once."""
    for kind in AbsenceType:
        assert kind.phrase, f"{kind.name} has no mid-sentence name"
        assert kind.phrase[0].islower() or kind.phrase.isupper(), (
            f"{kind.name} reads as {kind.phrase!r} mid-sentence"
        )
    assert AbsenceType.FLEXI.phrase == "TOIL", "an acronym stays an acronym"
    assert AbsenceType.ANNUAL.phrase == "annual leave"
