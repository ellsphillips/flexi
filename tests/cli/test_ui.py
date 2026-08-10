"""Flexi's own prompts, driven without a terminal.

The whole point of splitting `keys`, `rail` and `menu` out of `prompt` is that
everything a person sees is a pure function of a value. A menu is pressed by
handing it a key and reading what comes back, so the interaction is tested at
full speed with nothing attached and no sleeping.
"""

from __future__ import annotations

import pytest
from rich.text import Text

from flexi.cli.ui import rail
from flexi.cli.ui.keys import Key, decode, incomplete
from flexi.cli.ui.menu import Menu, Option


def styles_on(line: Text, needle: str) -> list[str]:
    """Every non-empty style covering the first character of `needle`."""
    start = line.plain.index(needle)
    return [
        str(span.style)
        for span in line.spans
        if span.start <= start < span.end and str(span.style)
    ]


# -- keys --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ("\x1b[A", Key.UP),
        ("\x1b[B", Key.DOWN),
        ("\x1bOA", Key.UP),
        ("\x1bOB", Key.DOWN),
        ("k", Key.UP),
        ("j", Key.DOWN),
        ("\r", Key.ENTER),
        ("\n", Key.ENTER),
        (" ", Key.ENTER),
        ("\x1b", Key.QUIT),
        ("q", Key.QUIT),
        ("\x03", Key.ABORT),
        ("\x04", Key.ABORT),
        ("z", Key.UNKNOWN),
    ],
)
def test_the_terminal_vocabulary(sequence: str, expected: Key) -> None:
    assert decode(sequence) is expected


def test_application_cursor_mode_is_read_too() -> None:
    """Textual leaves the terminal in application mode.

    `flexi init` runs straight after the setup form closes, so reading only the
    default mode would break the arrows in exactly the place they are needed.
    """
    assert decode("\x1bOA") is decode("\x1b[A")


def test_an_escape_alone_is_only_known_once_nothing_follows() -> None:
    assert incomplete("\x1b")
    assert incomplete("\x1b[")
    assert not incomplete("\x1b[A")
    assert not incomplete("j")


# -- the rail ----------------------------------------------------------------


def test_the_live_rail_is_heavy_and_a_settled_one_is_hairline() -> None:
    """The weight is the whole signal.

    That the two glyphs differ is a Literal comparison mypy settles statically,
    so asserting it here would be checking the type checker.
    """
    assert rail.HEAVY in rail.body(tone=rail.Tone.LIVE).plain
    assert rail.HAIRLINE in rail.body().plain


def test_the_rail_colours_itself_and_nothing_after_it() -> None:
    """`Text(s, style=...)` styles the whole object, including later appends.

    Building the rail that way painted every label in the accent, which left the
    weight and the colour both saying "live" on every line of the flow.
    """
    assert styles_on(rail.body("plain words"), "plain words") == []
    assert styles_on(rail.body("plain words"), rail.HAIRLINE) != []


def test_a_destructive_row_is_red_before_you_land_on_it() -> None:
    """Finding out by arrowing onto it is one keystroke too late."""
    resting = rail.option("Start again", "erase everything", picked=False, grave=True)
    assert styles_on(resting, "Start again") == [rail.Tone.GRAVE.style]


def test_an_ordinary_row_is_left_in_default_ink_until_picked() -> None:
    assert (
        styles_on(rail.option("Open Flexi", "hint", picked=False), "Open Flexi") == []
    )
    picked = rail.option("Open Flexi", "hint", picked=True)
    assert styles_on(picked, "Open Flexi") == [f"bold {rail.Tone.LIVE.style}"]


def test_the_cursor_carries_the_selection_as_well_as_the_colour() -> None:
    """Colour alone would leave somebody who cannot see teal with no cursor."""
    assert rail.CURSOR in rail.option("a", "", picked=True).plain
    assert rail.CURSOR not in rail.option("a", "", picked=False).plain


def test_hints_line_up_in_a_column() -> None:
    short = rail.option("Open", "hint", picked=False)
    long = rail.option("Change settings", "hint", picked=False)
    assert short.plain.index("hint") == long.plain.index("hint")


# -- the menu ----------------------------------------------------------------


def a_menu() -> Menu:
    return Menu(
        "What would you like to do?",
        (
            Option("open", "Open Flexi"),
            Option("settings", "Change settings"),
            Option("reset", "Start again", grave=True),
        ),
    )


def test_it_starts_on_the_safe_option() -> None:
    """The destructive row is never the one under the cursor on arrival."""
    assert a_menu().picked.value == "open"
    assert not a_menu().picked.grave


def test_arrows_move_the_cursor() -> None:
    assert a_menu().press(Key.DOWN).picked.value == "settings"
    assert a_menu().press(Key.DOWN).press(Key.DOWN).picked.value == "reset"


def test_it_wraps_at_both_ends() -> None:
    assert a_menu().press(Key.UP).picked.value == "reset"
    walked = a_menu().press(Key.DOWN).press(Key.DOWN).press(Key.DOWN)
    assert walked.picked.value == "open"


def test_a_key_that_means_nothing_here_changes_nothing() -> None:
    menu = a_menu()
    assert menu.press(Key.UNKNOWN) == menu


def test_pressing_a_key_returns_a_new_menu() -> None:
    """Immutable, so drawing is a function of the value rather than of history."""
    menu = a_menu()
    assert menu.press(Key.DOWN) is not menu
    assert menu.cursor == 0


def test_every_option_is_drawn() -> None:
    drawn = "\n".join(line.plain for line in a_menu().render())
    for label in ("Open Flexi", "Change settings", "Start again"):
        assert label in drawn
    assert "↑↓ move" in drawn


def test_a_menu_with_nothing_on_it_is_a_bug() -> None:
    with pytest.raises(ValueError, match="at least one option"):
        Menu("nothing", ())
