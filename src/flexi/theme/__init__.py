"""The palette, read from the stylesheet and not restated here.

Textual scopes CSS variables to the file that declares them, so the PALETTE
block is parsed out of ``flexi.tcss`` and republished through a Textual
``Theme``. ``flexi.tcss`` is where a colour is chosen; :data:`FALLBACK` is a
second copy of the entries Python asks for by name, for a machine where that
file cannot be read.

Two traps: an undefined variable fails during CSS parse at startup, not at
render, and ``App.theme = "flexi"`` raises unless ``register_theme`` has run.
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

        Static consumers see :class:`textual.theme.Theme`; at runtime this
        protocol keeps annotation tools from importing the widget toolkit to
        resolve a return annotation.
        """

        name: str


# The rail: a line down the left margin, heavy through the step being answered
# and hairline through the rest. The terminal prompts and the setup screen both
# draw from here.
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
        Cell.AMENDED: "▒",
        Cell.ON: "█",
        Cell.LIVE: "▌",
    }
)
"""One cell of a punch strip, as a character.

Here and not in `components/`, which imports Textual: `flexi clock` draws the
strip on the terminal while the dashboard draws it in a widget.
"""

THEME_NAME: Final = "flexi"
THEME_PATH: Final[Path] = Path(__file__).with_name("flexi.tcss")

# Matches only top-level `$name: value;` declarations holding a literal. A value
# containing `$` is skipped: the parser does no substitution.
PALETTE_DECLARATION: Final = re.compile(
    r"^\s*\$([a-z0-9-]+)\s*:\s*([^;${}]+);", re.MULTILINE
)

# Used only if the stylesheet cannot be read. The values repeat the PALETTE
# block, and `tests/test_theme.py` asserts the two agree.
FALLBACK: Final[Mapping[str, str]] = MappingProxyType(
    {
        # Every colour Python asks for by name. The rest are only ever read as
        # `$c-...` from within the stylesheet, which needs no fallback.
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
        # Asked for by `flexi.cli.ui.onclock.CELL_TONES` for a booked day.
        "c-annual": "#8451C9",
    }
)


@cache
def palette(path: Path = THEME_PATH) -> Mapping[str, str]:
    """The `$c-*` colours declared at the top of ``flexi.tcss``.

    Cached: the stylesheet does not change under a running application. An
    unreadable file yields the fallback instead of raising, since the CSS load
    fails a moment later with an error that names the problem.

    Read-only, because the cache hands every caller the same object.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return MappingProxyType(dict(FALLBACK))
    found = {name: value.strip() for name, value in PALETTE_DECLARATION.findall(source)}
    return MappingProxyType(found or dict(FALLBACK))


def colour(name: str, fallback: str = "#FF00FF") -> str:
    """One palette entry, by name, without the leading ``$``.

    The default is magenta, so a colour that falls through to it shows up in
    the first screenshot.
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
            # Theme-only, and absent from flexi.tcss: Textual 8 substitutes a
            # `hatch:` colour twice when it is both declared and supplied, and
            # the property then sees four tokens.
            "c-hatch-empty": colour("c-line-soft"),
            "c-hatch-jump": colour("c-ink"),
        }
    )
    variables.update(
        {
            # Textual always paints a foreground on the cursor row, so the
            # highlighted row loses the punch strip and the signed delta.
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

    ``textual.theme`` is imported in the body, not at module scope: the command
    line reads the glyphs and the palette without ever drawing a widget.

    ``primary`` is the teal accent because Textual paints focus, selection and
    the primary button with it. ``error`` is the deficit red and ``success`` the
    surplus green, so a Textual-native widget lands on the colours the balance
    uses.
    """
    from textual.theme import Theme as TextualTheme

    return TextualTheme(
        name=THEME_NAME,
        primary=colour("c-accent"),
        # Textual reaches for `secondary` on a handful of widget accents, and
        # the second colour the palette means is the one a surplus wears.
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
