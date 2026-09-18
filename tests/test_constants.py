from flexi import constants
from flexi.constants import AbsenceType


def test_every_absence_type_reads_inside_a_sentence() -> None:
    """`label.lower()` turned "TOIL" into "toil" in six places at once."""
    for kind in AbsenceType:
        assert kind.phrase, f"{kind.name} has no mid-sentence name"
        assert kind.phrase[0].islower() or kind.phrase.isupper(), (
            f"{kind.name} reads as {kind.phrase!r} mid-sentence"
        )
    assert AbsenceType.FLEXI.phrase == "TOIL", "an acronym stays an acronym"
    assert AbsenceType.ANNUAL.phrase == "annual leave"


def test_every_absence_type_declares_its_details() -> None:
    """A member with no row is a KeyError on `.label`, at booking time.

    Read straight off the private table, so `flexi.constants` carries no
    function that exists for this assertion to call.
    """
    assert frozenset(AbsenceType) == frozenset(constants._DETAILS)
