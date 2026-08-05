"""The palette, read from the stylesheet rather than restated here.

Textual scopes CSS variables to the file that declares them, so a `$c-accent`
written in `flexi.tcss` would be invisible to any other stylesheet. Parsing the
palette out of `flexi.tcss` and republishing it through the Textual `Theme`
makes every name available application-wide while leaving exactly one place
where a colour is written down.

Two traps worth knowing before editing this module:

- **An undefined variable fails at startup**, during CSS parse, not at render.
  Anything a stylesheet references must be either in the PALETTE block or in
  :func:`theme_variables`.
- **`App.theme = "flexi"` raises if `register_theme` has not run.** Register in
  the app's ``__init__``, not ``on_mount``: Flexi can push the setup screen
  before ``on_mount`` completes and that is too late.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Final

from textual.theme import Theme

THEME_NAME: Final = "flexi"
THEME_PATH: Final[Path] = Path(__file__).with_name("flexi.tcss")

# Matches only top-level `$name: value;` declarations holding a literal. A value
# containing `$` is skipped deliberately: the parser does no substitution, and a
# half-resolved colour would be worse than an absent one.
_PALETTE_DECLARATION: Final = re.compile(
    r"^\s*\$([a-z0-9-]+)\s*:\s*([^;${}]+);", re.MULTILINE
)

# Fallbacks, used only if the stylesheet cannot be read. They are the same
# values as the PALETTE block; a mismatch here is a bug, and
# `tests/test_theme.py` asserts the two agree.
_FALLBACK: Final[dict[str, str]] = {
    "c-ink": "#0F0E0D",
    "c-surface": "#171614",
    "c-raised": "#201E1B",
    "c-line": "#2E2B27",
    "c-paper": "#EDE9E3",
    "c-cream": "#FAF8F4",
    "c-muted": "#9C948A",
    "c-accent": "#00AAAD",
    "c-accent-lift": "#4CDCDF",
    "c-surplus": "#2E9E52",
    "c-deficit": "#CE3E5D",
    "c-warning": "#C38406",
}


@cache
def palette(path: Path = THEME_PATH) -> dict[str, str]:
    """The `$c-*` colours declared at the top of ``flexi.tcss``.

    Cached because it is read once per process and the stylesheet does not
    change under a running application. A missing or unreadable file yields the
    fallback rather than raising: the CSS itself will fail loudly a moment
    later, and that error names the actual problem.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return dict(_FALLBACK)
    found = {name: value.strip() for name, value in _PALETTE_DECLARATION.findall(source)}
    return found or dict(_FALLBACK)


def colour(name: str, fallback: str = "#FF00FF") -> str:
    """One palette entry, by name, without the leading ``$``.

    The default is magenta on purpose: a colour that fell through to it is
    meant to be found in the first screenshot, not to blend in.
    """
    return palette().get(name, _FALLBACK.get(name, fallback))


def theme_variables() -> dict[str, str]:
    """Every palette entry, plus the Textual variables Flexi overrides.

    Published through ``Theme.variables``, which Textual merges wholesale into
    the CSS variables available to every stylesheet.
    """
    variables = dict(palette())
    variables.update(
        {
            # Theme-only, and deliberately NOT declared in flexi.tcss.
            #
            # Textual 8 mis-parses `hatch:` when its colour variable is both
            # declared in the stylesheet and supplied through the theme — the
            # value is substituted twice and the property sees four tokens where
            # it wants two or three. Every other property survives it, which is
            # why this looks arbitrary until you hit it.
            #
            # Derived from the palette rather than written out again, so the
            # PALETTE block stays the single place a colour is chosen.
            "c-hatch-empty": colour("c-line-soft", "#232019"),
            "c-hatch-jump": colour("c-ink"),
        }
    )
    variables.update(
        {
            # Textual always paints a foreground on the cursor row, so a
            # highlighted row loses its cells' own colours — which in the
            # records table is the punch strip and the signed delta, the two
            # things the reader is looking at. It cannot be prevented, so it is
            # made quiet instead: a faint band when the table is not focused, a
            # teal one when it is.
            "block-cursor-text-style": "none",
            "block-cursor-background": colour("c-accent-deep"),
            "block-cursor-foreground": colour("c-cream"),
            "block-cursor-blurred-background": colour("c-line-soft", "#232019"),
            "block-cursor-blurred-foreground": colour("c-muted"),
            "block-cursor-blurred-text-style": "none",
            "footer-key-foreground": colour("c-accent-lift"),
            "footer-description-foreground": colour("c-muted"),
            "input-selection-background": f"{colour('c-accent')} 35%",
            "input-cursor-background": colour("c-accent"),
            "input-cursor-foreground": colour("c-ink"),
            "border-blurred": colour("c-line"),
            "scrollbar": colour("c-line"),
            "scrollbar-hover": colour("c-ash", "#7A736A"),
            "scrollbar-active": colour("c-accent"),
        }
    )
    return variables


def flexi_theme() -> Theme:
    """The Flexi palette as a Textual theme.

    ``primary`` is the teal accent because Textual paints focus, selection and
    the primary button with it, and those are exactly the moments that should
    carry the accent. ``error`` is the deficit red and ``success`` the surplus
    green, so a Textual-native widget reporting state lands on the same two
    colours the balance uses.
    """
    return Theme(
        name=THEME_NAME,
        primary=colour("c-accent"),
        # Green: Textual reaches for `secondary` on a handful of widget accents,
        # and the second colour Flexi actually means is the one a surplus wears.
        secondary=colour("c-surplus", "#2E9E52"),
        accent=colour("c-accent-lift"),
        warning=colour("c-warning"),
        error=colour("c-deficit", "#CE3E5D"),
        success=colour("c-surplus", "#2E9E52"),
        foreground=colour("c-paper"),
        background=colour("c-ink"),
        surface=colour("c-surface"),
        panel=colour("c-raised"),
        dark=True,
        variables=theme_variables(),
    )


__all__ = ["THEME_NAME", "THEME_PATH", "colour", "flexi_theme", "palette", "theme_variables"]
