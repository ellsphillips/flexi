"""The redraw protocol, tested as arithmetic rather than through a screen.

A module never calls another module's ``rebuild()``. It posts a message with a
scope, and every module whose ``WATCHES`` intersects that scope redraws --
``rebuild_if`` is one line, ``if scope & self.WATCHES``. So the whole protocol
rests on what the flags in this module mean, and that is a question about
values: it needs no application, and answering it here is what makes a failure
point at the scope rather than at whichever widget noticed first.
"""

from __future__ import annotations

from datetime import date

import pytest

from flexi.messages import DataChanged, DateSelected, Scope

# The two real subscriptions in the application, quoted rather than imported so
# that a change to either has to be made deliberately in both places.
CLOCK_MODULE = Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS
EVERYTHING = Scope.ALL


# -- scopes ------------------------------------------------------------------


@pytest.mark.parametrize("scope", list(Scope))
def test_every_scope_is_covered_by_all(scope: Scope) -> None:
    """A module watching `ALL` must redraw for a kind of change added later.

    `ALL` is written out by hand, so a fifth flag left out of it would leave
    the modules that asked for everything quietly ignoring it.
    """
    assert scope & Scope.ALL


def test_the_scopes_do_not_overlap() -> None:
    """Distinct bits, so `scope & WATCHES` is an answer and not a coincidence."""
    seen = Scope.NONE
    for scope in Scope:
        assert not scope & seen, f"{scope} shares a bit with an earlier scope"
        seen |= scope


def test_a_module_redraws_only_for_the_changes_it_asked_about() -> None:
    """Moving the view is not a reason to rebuild a clock.

    The whole point of the scope is that a week's worth of arrow presses does
    not rebuild every module on the dashboard.
    """
    assert DataChanged(Scope.CLOCK).scope & CLOCK_MODULE
    assert not DataChanged(Scope.PERIOD).scope & CLOCK_MODULE
    assert DataChanged(Scope.PERIOD).scope & EVERYTHING


def test_a_write_that_does_not_say_what_it_touched_redraws_everything() -> None:
    """The safe default.

    A service that forgets to name a scope costs a redraw; one that defaulted to
    `NONE` would leave a stale balance on screen, and nothing on screen would
    say it was stale.
    """
    assert DataChanged().scope is Scope.ALL
    assert DataChanged().scope & CLOCK_MODULE


def test_nothing_watches_the_empty_scope() -> None:
    """`NONE` is the identity for `|`, used to accumulate a subscription.

    It has to intersect nothing, including the module that asked for
    everything, or an accumulator starting from it would redraw the dashboard.
    """
    assert not Scope.NONE & Scope.ALL


# -- what each message carries -----------------------------------------------


def test_a_scoped_change_carries_the_scope_it_was_given() -> None:
    assert DataChanged(Scope.ABSENCE).scope is Scope.ABSENCE


def test_a_picked_date_arrives_under_a_name_that_is_not_the_argument() -> None:
    """`when` reads at the call site, `date` reads at the handler.

    Mixing the two up gives an `AttributeError` inside a message handler, which
    Textual logs and swallows, so the calendar simply stops responding.
    """
    assert DateSelected(date(2025, 6, 2)).date == date(2025, 6, 2)
