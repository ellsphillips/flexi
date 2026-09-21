"""The palette, and the promise that it is written down once.

Every colour Flexi paints with lives in the PALETTE block of ``flexi.tcss``.
This module parses that block and republishes it through a Textual ``Theme``,
because Textual scopes a stylesheet's ``$`` variables to that stylesheet alone.
An undefined variable fails during CSS parse at startup, and a colour that
falls through to a default is invisible outside a screenshot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.color import Color

from flexi import theme

MAGENTA = "#FF00FF"
"""What `colour()` returns for an unknown name, loud enough to spot."""


def stylesheet(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "flexi.tcss"
    path.write_text(body, encoding="utf-8")
    return path


# --- parsing the stylesheet -------------------------------------------------


def test_fallback_palette_agrees_with_stylesheet() -> None:
    """The hard-coded copy is what a machine with no stylesheet paints with."""
    parsed = theme.palette()

    assert {name: parsed.get(name) for name in theme.FALLBACK} == theme.FALLBACK


def test_palette_is_read_from_the_file(tmp_path: Path) -> None:
    """A top-level `$name: value;` is a palette entry, whatever the value is."""
    path = stylesheet(tmp_path, "$c-accent: #123456;\n$c-ink: #000001;\n")

    assert theme.palette(path) == {"c-accent": "#123456", "c-ink": "#000001"}


def test_using_a_colour_does_not_declare_one(tmp_path: Path) -> None:
    """A rule that reads `$c-ink` must not add an entry called `background`."""
    path = stylesheet(
        tmp_path, "$c-ink: #0F0E0D;\nScreen {\n    background: $c-ink;\n}\n"
    )

    assert theme.palette(path) == {"c-ink": "#0F0E0D"}


def test_derived_colour_is_left_out(tmp_path: Path) -> None:
    """The parser does no substitution, so `$c-accent 30%` is not a colour."""
    path = stylesheet(tmp_path, "$c-accent: #00AAAD;\n$c-glow: $c-accent 30%;\n")

    assert "c-glow" not in theme.palette(path)


def test_unreadable_stylesheet_gives_the_fallback(tmp_path: Path) -> None:
    """A missing stylesheet is the CSS parser's error to raise, not this one's."""
    assert theme.palette(tmp_path / "gone.tcss") == theme.FALLBACK


def test_stylesheet_with_no_palette_gives_the_fallback(tmp_path: Path) -> None:
    """Publishing nothing leaves every `$c-` reference undefined at parse time."""
    path = stylesheet(tmp_path, "/* the palette moved */\nScreen { background: red; }")

    assert theme.palette(path) == theme.FALLBACK


# --- one colour at a time ---------------------------------------------------


def test_colour_is_looked_up_without_its_dollar() -> None:
    assert theme.colour("c-accent") == theme.palette()["c-accent"]


def test_unknown_colour_comes_back_magenta() -> None:
    """Magenta is in no Flexi palette, so a name that fell through shows up."""
    assert theme.colour("c-not-a-colour") == MAGENTA


def test_caller_may_name_its_own_fallback() -> None:
    """Used where a palette entry is optional and a neighbour will do."""
    assert theme.colour("c-not-a-colour", "#232019") == "#232019"


# --- the theme --------------------------------------------------------------


def test_every_palette_entry_reaches_the_theme() -> None:
    """`Theme.variables` is the only route a stylesheet has to these names."""
    assert theme.palette().items() <= theme.theme_variables().items()


def test_hatch_colours_come_only_from_the_theme() -> None:
    """A hatch colour declared in both places is substituted twice.

    Textual 8 then sees four tokens in the property and fails to parse it.
    """
    variables = theme.theme_variables()

    assert "c-hatch-empty" not in theme.palette()
    assert "c-hatch-jump" not in theme.palette()
    assert variables["c-hatch-jump"] == theme.colour("c-ink")


def test_cursor_row_text_style_is_cleared() -> None:
    """Textual paints a foreground over the punch strip and the signed delta."""
    variables = theme.theme_variables()

    assert variables["block-cursor-text-style"] == "none"
    assert variables["block-cursor-blurred-text-style"] == "none"


def test_no_theme_variable_falls_through_to_magenta() -> None:
    """A `colour()` call for a renamed entry is valid CSS and the wrong colour."""
    fallen = [
        name for name, value in theme.theme_variables().items() if value == MAGENTA
    ]

    assert fallen == []


def test_fallback_covers_every_name_python_asks_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FALLBACK` holds every name Python asks for, not a subset of the palette.

    Everything else is read as `$c-...` from a stylesheet that has parsed.
    """
    monkeypatch.setattr(theme, "palette", lambda: dict(theme.FALLBACK))

    fallen = [
        name for name, value in theme.theme_variables().items() if value == MAGENTA
    ]

    assert fallen == []


def test_palette_cannot_be_rewritten_by_a_reader() -> None:
    """The palette is cached, so every caller holds the same object."""
    with pytest.raises(TypeError):
        theme.palette()["c-ink"] = "#FFFFFF"  # type: ignore[index]


@pytest.mark.parametrize(
    "attribute",
    ["primary", "secondary", "accent", "warning", "error", "success"],
)
def test_theme_colours_are_parseable(attribute: str) -> None:
    """An unparseable colour fails during CSS parse, before any screen exists."""
    Color.parse(getattr(theme.flexi_theme(), attribute))


def test_state_wears_the_balance_colours() -> None:
    """A Textual-native widget reports on the red a deficit already wears."""
    built = theme.flexi_theme()

    assert built.error == theme.colour("c-deficit")
    assert built.success == theme.colour("c-surplus")
    assert built.primary == theme.colour("c-accent")


def test_theme_is_registered_as_flexi() -> None:
    """`App.theme` raises unless the name matches a registered theme."""
    assert theme.flexi_theme().name == theme.THEME_NAME == "flexi"


def test_theme_is_dark_and_carries_the_palette() -> None:
    """A theme built without the variables leaves every `$c-` undefined."""
    built = theme.flexi_theme()

    assert built.dark
    assert built.variables == theme.theme_variables()
