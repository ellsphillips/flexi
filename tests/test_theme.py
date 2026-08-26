"""The palette, and the promise that it is written down exactly once.

Every colour Flexi paints with lives in the PALETTE block of ``flexi.tcss``.
This module parses that block and republishes it through a Textual ``Theme``,
because Textual scopes a stylesheet's ``$`` variables to that stylesheet alone.
Two traps make the parsing worth testing rather than eyeballing: an undefined
variable fails during CSS parse at startup rather than at render, and a colour
that falls through to a default is invisible until somebody looks at a
screenshot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.color import Color

from flexi import theme

MAGENTA = "#FF00FF"
"""What `colour()` returns for a name nothing knows. Loud on purpose."""


def stylesheet(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "flexi.tcss"
    path.write_text(body, encoding="utf-8")
    return path


# -- parsing the stylesheet --------------------------------------------------


def test_the_fallback_palette_agrees_with_the_stylesheet() -> None:
    """The hard-coded copy exists for a stylesheet that cannot be read.

    Two lists of colours that are allowed to disagree are one list of colours
    and one lie, and the lie only ever shows up on the machine where the file
    is missing -- which is the worst possible place to discover it.
    """
    parsed = theme.palette()

    assert {name: parsed.get(name) for name in theme.FALLBACK} == theme.FALLBACK


def test_the_palette_is_read_from_the_file_rather_than_restated_here(
    tmp_path: Path,
) -> None:
    """A top-level `$name: value;` is a palette entry, whatever the value is."""
    path = stylesheet(tmp_path, "$c-accent: #123456;\n$c-ink: #000001;\n")

    assert theme.palette(path) == {"c-accent": "#123456", "c-ink": "#000001"}


def test_using_a_colour_does_not_declare_one(tmp_path: Path) -> None:
    """Only declarations count.

    A rule that *reads* `$c-ink` must not add an entry called `background`, or
    the theme would republish a property name as though it were a colour.
    """
    path = stylesheet(
        tmp_path, "$c-ink: #0F0E0D;\nScreen {\n    background: $c-ink;\n}\n"
    )

    assert theme.palette(path) == {"c-ink": "#0F0E0D"}


def test_a_colour_defined_in_terms_of_another_is_left_out(tmp_path: Path) -> None:
    """The parser does no substitution, so it must not pretend to.

    Publishing `c-glow` as the literal string `$c-accent 30%` would hand
    Textual a value it resolves against a variable this stylesheet no longer
    owns; a half-resolved colour is worse than an absent one, which at least
    arrives as magenta.
    """
    path = stylesheet(tmp_path, "$c-accent: #00AAAD;\n$c-glow: $c-accent 30%;\n")

    assert "c-glow" not in theme.palette(path)


def test_a_stylesheet_that_cannot_be_read_gives_the_fallback(tmp_path: Path) -> None:
    """A missing stylesheet is the CSS parser's news to break, not this one's.

    Raising here would take the application down before the parser had a chance
    to say what was actually wrong with the file.
    """
    assert theme.palette(tmp_path / "gone.tcss") == theme.FALLBACK


def test_a_stylesheet_with_no_palette_left_in_it_gives_the_fallback(
    tmp_path: Path,
) -> None:
    """An empty parse is a failed parse.

    Publishing nothing would leave every `$c-` reference in every screen's
    stylesheet undefined, and Textual fails those at parse time -- a wall of
    errors naming the symptom rather than the cause.
    """
    path = stylesheet(tmp_path, "/* the palette moved */\nScreen { background: red; }")

    assert theme.palette(path) == theme.FALLBACK


# -- one colour at a time ----------------------------------------------------


def test_a_colour_is_looked_up_by_name_without_its_dollar() -> None:
    assert theme.colour("c-accent") == theme.palette()["c-accent"]


def test_a_colour_nothing_knows_about_comes_back_shouting() -> None:
    """Magenta is in no Flexi palette.

    A name that fell through to it is found in the first screenshot rather than
    blending into the graphite.
    """
    assert theme.colour("c-not-a-colour") == MAGENTA


def test_a_caller_may_name_the_colour_it_would_rather_fall_back_to() -> None:
    """Used where a palette entry is optional and a sensible neighbour exists."""
    assert theme.colour("c-not-a-colour", "#232019") == "#232019"


# -- the theme ---------------------------------------------------------------


def test_every_palette_entry_reaches_the_theme() -> None:
    """`Theme.variables` is merged wholesale.

    It is the only route a screen stylesheet has to these names.
    """
    assert theme.palette().items() <= theme.theme_variables().items()


def test_the_hatch_colours_are_supplied_by_the_theme_and_by_nothing_else() -> None:
    """Declared in both places, a hatch colour is substituted twice.

    Textual 8 then sees four tokens in the property and fails to parse it. These
    two are theme-only, deliberately, and their values still come from the
    palette so a colour is chosen in one place.
    """
    variables = theme.theme_variables()

    assert "c-hatch-empty" not in theme.palette()
    assert "c-hatch-jump" not in theme.palette()
    assert variables["c-hatch-jump"] == theme.colour("c-ink")


def test_the_cursor_row_is_quietened_rather_than_left_to_textual() -> None:
    """Textual always paints a foreground on the highlighted row.

    Left alone it takes the punch strip and the signed delta with it.
    """
    variables = theme.theme_variables()

    assert variables["block-cursor-text-style"] == "none"
    assert variables["block-cursor-blurred-text-style"] == "none"


def test_no_variable_the_theme_publishes_fell_through_to_magenta() -> None:
    """The one test that catches a palette entry renamed in `flexi.tcss`.

    Nothing else does: a `colour()` call for a name that no longer exists is
    valid Python, valid CSS and the wrong colour on every screen that uses it.
    """
    fallen = [
        name for name, value in theme.theme_variables().items() if value == MAGENTA
    ]

    assert fallen == []


@pytest.mark.parametrize(
    "attribute",
    ["primary", "secondary", "accent", "warning", "error", "success"],
)
def test_the_theme_hands_textual_colours_it_can_parse(attribute: str) -> None:
    """An unparseable colour fails during CSS parse at startup.

    That is before there is a screen to report it on, so it is worth settling
    here instead.
    """
    Color.parse(getattr(theme.flexi_theme(), attribute))


def test_state_wears_the_same_colours_the_balance_does() -> None:
    """One red and one green, whoever is doing the reporting.

    A Textual-native widget reporting an error must land on the red a deficit
    wears rather than on Textual's own: two reds on one screen read as two
    different kinds of bad news.
    """
    built = theme.flexi_theme()

    assert built.error == theme.colour("c-deficit")
    assert built.success == theme.colour("c-surplus")
    assert built.primary == theme.colour("c-accent")


def test_the_theme_is_registered_under_the_name_the_application_asks_for() -> None:
    """`App.theme = "flexi"` raises unless the registered theme matches.

    It is set from a bare string in two places, so the name is worth pinning.
    """
    assert theme.flexi_theme().name == theme.THEME_NAME == "flexi"


def test_the_theme_is_dark_and_carries_the_palette() -> None:
    """The theme is the only route the palette has into a screen stylesheet.

    `theme_variables` reaching every colour is settled above; this is the other
    half of the journey. A theme built without them leaves every `$c-` reference
    in every stylesheet undefined, which Textual fails at CSS parse -- before
    there is a screen to report it on.
    """
    built = theme.flexi_theme()

    assert built.dark
    assert built.variables == theme.theme_variables()
