"""The palette, read from the stylesheet rather than restated here.

Textual scopes CSS variables to the file that declares them, so the PALETTE
block is parsed out of ``flexi.tcss`` and republished through a Textual
``Theme``. That leaves exactly one place where a colour is written down.

Two traps: an undefined variable fails during CSS parse at startup rather than
at render, and ``App.theme = "flexi"`` raises unless ``register_theme`` has
already run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

from flexi.domain.punch import Cell

if TYPE_CHECKING:
    from textual.theme import Theme
else:

    class Theme(Protocol):
        """Runtime shape of the Textual theme returned by :func:`flexi_theme`.

        Static consumers see :class:`textual.theme.Theme`; runtime annotation
        tools see this lightweight protocol, so reading the shared palette does
        not import the widget toolkit merely to resolve a return annotation.
        """

        name: str


# The rail. Flexi's structural vocabulary, kept beside the palette because it
# is part of the same design system: a line down the left margin, heavy through
# the step being answered and hairline through the rest, with a marker at each
# moment. The terminal prompts and the setup screen both draw from here, so the
# two are visibly the same product rather than two that happen to share a name.
RAIL_LIVE: Final = "┃"
RAIL_SETTLED: Final = "│"
MARK_LIVE: Final = "◆"
MARK_DONE: Final = "●"
MARK_GRAVE: Final = "▲"
CURSOR: Final = "▸"
TAIL: Final = "╰"

CELL_GLYPHS: Final[Mapping[Cell, str]] = MappingProxyType(
    {
        Cell.OFF: "─",
        Cell.BREAK: "·",
        Cell.TARGET: "┊",
        Cell.ABSENCE: "▓",
        Cell.HOLIDAY: "░",
        Cell.ON: "█",
        Cell.LIVE: "▌",
    }
)
"""One cell of a punch strip, as a character.

Beside the rail for the same reason the rail is here: the strip is Flexi's
other structural idea, and `flexi clock` draws it on the terminal while the
dashboard draws it in a widget. It lived in `components/punch.py`, which
imports Textual, so the command line could not read it without loading a
widget toolkit to print seven characters.
"""

THEME_NAME: Final = "flexi"
THEME_PATH: Final[Path] = Path(__file__).with_name("flexi.tcss")

# Matches only top-level `$name: value;` declarations holding a literal. A value
# containing `$` is skipped deliberately: the parser does no substitution, and a
# half-resolved colour would be worse than an absent one.
PALETTE_DECLARATION: Final = re.compile(
    r"^\s*\$([a-z0-9-]+)\s*:\s*([^;${}]+);", re.MULTILINE
)

# Fallbacks, used only if the stylesheet cannot be read. They are the same
# values as the PALETTE block; a mismatch here is a bug, and
# `tests/test_theme.py` asserts the two agree.
FALLBACK: Final[Mapping[str, str]] = MappingProxyType(
    {
        # Every colour Python asks for by name. The other twenty-six declarations
        # in the stylesheet are only ever read as `$c-...` from the stylesheet
        # itself, which could not be read at all if it could not be parsed -- so a
        # fallback for one of those would answer a question nobody could ask.
        #
        # Five of these were also written out as literal `fallback=` arguments at
        # call sites, where three were unreachable, so the module that says there
        # is "exactly one place where a colour is written down" had three.
        "c-ink": "#0F0E0D",
        "c-surface": "#171614",
        "c-raised": "#201E1B",
        "c-line": "#2E2B27",
        "c-line-soft": "#232019",
        "c-ash": "#7A736A",
        "c-paper": "#EDE9E3",
        "c-cream": "#FAF8F4",
        "c-muted": "#9C948A",
        "c-accent": "#00AAAD",
        "c-accent-lift": "#4CDCDF",
        "c-accent-deep": "#003031",
        "c-surplus": "#2E9E52",
        "c-deficit": "#CE3E5D",
        "c-warning": "#C38406",
    }
)


@cache
def palette(path: Path = THEME_PATH) -> Mapping[str, str]:
    """The `$c-*` colours declared at the top of ``flexi.tcss``.

    Cached because it is read once per process and the stylesheet does not
    change under a running application. A missing or unreadable file yields the
    fallback rather than raising: the CSS itself will fail loudly a moment
    later, and that error names the actual problem.

    Read-only, because the cache means every caller is handed the same object.
    Returned as a plain dict, one `palette()["c-ink"] = ...` would repaint the
    application for the rest of the process -- and the module already knew,
    which is why `theme_variables` copies before it adds to it.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return MappingProxyType(dict(FALLBACK))
    found = {name: value.strip() for name, value in PALETTE_DECLARATION.findall(source)}
    return MappingProxyType(found or dict(FALLBACK))


def colour(name: str, fallback: str = "#FF00FF") -> str:
    """One palette entry, by name, without the leading ``$``.

    The default is magenta on purpose: a colour that fell through to it is
    meant to be found in the first screenshot, not to blend in.
    """
    return palette().get(name, FALLBACK.get(name, fallback))


def theme_variables() -> dict[str, str]:
    """Every palette entry, plus the Textual variables Flexi overrides.

    Published through ``Theme.variables``, which Textual merges wholesale into
    the CSS variables available to every stylesheet.
    """
    variables = dict(palette())
    variables.update(
        {
            # Theme-only, deliberately absent from flexi.tcss: Textual 8
            # substitutes a `hatch:` colour twice when it is both declared and
            # supplied, and the property then sees four tokens. Derived from the
            # palette so it stays the one place a colour is chosen.
            "c-hatch-empty": colour("c-line-soft"),
            "c-hatch-jump": colour("c-ink"),
        }
    )
    variables.update(
        {
            # Textual always paints a foreground on the cursor row, so the
            # highlighted row loses the punch strip and the signed delta. It
            # cannot be prevented, so it is made quiet.
            "block-cursor-text-style": "none",
            "block-cursor-background": colour("c-accent-deep"),
            "block-cursor-foreground": colour("c-cream"),
            "block-cursor-blurred-background": colour("c-line-soft"),
            "block-cursor-blurred-foreground": colour("c-muted"),
            "block-cursor-blurred-text-style": "none",
            "footer-key-foreground": colour("c-accent-lift"),
            "footer-description-foreground": colour("c-muted"),
            "input-selection-background": f"{colour('c-accent')} 35%",
            "input-cursor-background": colour("c-accent"),
            "input-cursor-foreground": colour("c-ink"),
            "border-blurred": colour("c-line"),
            "scrollbar": colour("c-line"),
            "scrollbar-hover": colour("c-ash"),
            "scrollbar-active": colour("c-accent"),
        }
    )
    return variables


def flexi_theme() -> Theme:
    """The Flexi palette as a Textual theme.

    ``textual.theme`` is imported here rather than at module scope. This module
    is the design system -- the glyphs and the palette parsed out of
    `flexi.tcss` -- and the command line reads both without ever drawing a
    Textual widget. Importing Textual to hand a prompt a colour cost the
    ``flexi init`` rail a hundred and fifty milliseconds.

    ``primary`` is the teal accent because Textual paints focus, selection and
    the primary button with it, and those are exactly the moments that should
    carry the accent. ``error`` is the deficit red and ``success`` the surplus
    green, so a Textual-native widget reporting state lands on the same two
    colours the balance uses.
    """
    from textual.theme import Theme as TextualTheme

    return TextualTheme(
        name=THEME_NAME,
        primary=colour("c-accent"),
        # Green: Textual reaches for `secondary` on a handful of widget accents,
        # and the second colour Flexi actually means is the one a surplus wears.
        secondary=colour("c-surplus"),
        accent=colour("c-accent-lift"),
        warning=colour("c-warning"),
        error=colour("c-deficit"),
        success=colour("c-surplus"),
        foreground=colour("c-paper"),
        background=colour("c-ink"),
        surface=colour("c-surface"),
        panel=colour("c-raised"),
        dark=True,
        variables=theme_variables(),
    )


__all__ = (
    "CELL_GLYPHS",
    "CURSOR",
    "FALLBACK",
    "MARK_DONE",
    "MARK_GRAVE",
    "MARK_LIVE",
    "PALETTE_DECLARATION",
    "RAIL_LIVE",
    "RAIL_SETTLED",
    "TAIL",
    "THEME_NAME",
    "THEME_PATH",
    "Theme",
    "colour",
    "flexi_theme",
    "palette",
    "theme_variables",
)
