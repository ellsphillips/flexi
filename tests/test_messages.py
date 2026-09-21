"""The redraw protocol, tested as arithmetic.

A module never calls another module's ``rebuild()``. The screen invalidates the
ledger once and calls ``refresh_modules(scope)``; every module whose
``WATCHES`` intersects that scope redraws. The whole protocol rests on what the
flags here mean, which is a question about values.
"""

from __future__ import annotations

from datetime import date

import pytest

from flexi.messages import BankHolidayRefreshCompleted, DateSelected, Scope

# The two real subscriptions in the application, quoted so that changing either
# has to be done in both places.
CLOCK_MODULE = Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS
EVERYTHING = Scope.ALL


# ---- scopes ----


@pytest.mark.parametrize("scope", list(Scope))
def test_every_scope_is_covered_by_all(scope: Scope) -> None:
    """`ALL` is written out by hand, so a new flag has to be added to it."""
    assert scope & Scope.ALL


def test_scopes_do_not_overlap() -> None:
    """Distinct bits, so `scope & WATCHES` is an answer and not a coincidence."""
    seen = Scope.NONE
    for scope in Scope:
        assert not scope & seen, f"{scope} shares a bit with an earlier scope"
        seen |= scope


def test_module_redraws_only_for_its_own_scopes() -> None:
    """Moving the view is not a reason to rebuild a clock."""
    assert Scope.CLOCK & CLOCK_MODULE
    assert not Scope.PERIOD & CLOCK_MODULE
    assert Scope.PERIOD & EVERYTHING


def test_nothing_watches_the_empty_scope() -> None:
    """`NONE` is the identity for `|`, which accumulates a subscription."""
    assert not Scope.NONE & Scope.ALL


# ---- what each message carries ----


def test_date_selected_exposes_date() -> None:
    """The argument is named `when` and the attribute is named `date`.

    Reaching for the wrong one raises inside a message handler, which Textual
    logs and swallows, so the calendar stops responding with nothing on screen.
    """
    assert DateSelected(date(2025, 6, 2)).date == date(2025, 6, 2)


def test_refresh_completed_carries_payload_and_flag() -> None:
    payload: object = {"scotland": {"events": []}}

    message = BankHolidayRefreshCompleted(payload, forced=True)

    assert message.payload is payload
    assert message.forced is True
