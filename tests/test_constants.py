from flexi.constants import AbsenceType, undeclared_types


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

    Checked here rather than at import, where it cost three module-level
    temporaries that outlived the check by the length of the process.
    """
    assert undeclared_types() == frozenset()
