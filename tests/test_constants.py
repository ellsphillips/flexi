from flexi import constants
from flexi.constants import AbsenceType


def test_every_absence_type_reads_mid_sentence() -> None:
    """`label.lower()` turns "TOIL" into "toil", so each kind carries a phrase."""
    for kind in AbsenceType:
        assert kind.phrase, f"{kind.name} has no mid-sentence name"
        assert kind.phrase[0].islower() or kind.phrase.isupper(), (
            f"{kind.name} reads as {kind.phrase!r} mid-sentence"
        )
    assert AbsenceType.FLEXI.phrase == "TOIL", "an acronym stays an acronym"
    assert AbsenceType.ANNUAL.phrase == "annual leave"


def test_every_absence_type_declares_its_details() -> None:
    """A member with no row is a `KeyError` on `.label`, at booking time.

    Read off the private table, then through the properties the booking path
    uses, so a row that is present and blank fails as well as a missing one.
    """
    assert frozenset(AbsenceType) == frozenset(constants._DETAILS)
    for kind in AbsenceType:
        assert kind.label, kind.name
        assert kind.short, kind.name
        assert kind.token, kind.name
