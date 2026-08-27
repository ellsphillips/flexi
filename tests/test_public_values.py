"""Published lookup tables are values, not shared mutable state."""

from __future__ import annotations

from types import MappingProxyType
from typing import Protocol, cast

import pytest

from flexi.cli.leave import PORTION_WORDS, VERDICT_NOTE
from flexi.cli.ui.keys import KEYS
from flexi.cli.ui.onclock import CELL_TONES
from flexi.cli.ui.prompt import WINDOWS_SCANCODES
from flexi.components.chrome import NAV_BY_SCREEN
from flexi.components.common import GAUGE_TONE_STYLES, TONE_CLASSES
from flexi.components.modules.monthview import KIND_CLASSES
from flexi.components.punch import BASE_STYLES
from flexi.components.splash import LETTER_GLYPHS
from flexi.components.yearcalendar import PORTION_GLYPH
from flexi.domain.dates import OFFSET_UNITS, RELATIVE_DAYS
from flexi.screens.dashboard import JUMP_TARGETS
from flexi.theme import CELL_GLYPHS, FALLBACK


class Settable(Protocol):
    """The mutation deliberately attempted below."""

    def __setitem__(self, key: object, value: object, /) -> None: ...


@pytest.mark.parametrize(
    "published",
    [
        pytest.param(PORTION_WORDS, id="CLI portion vocabulary"),
        pytest.param(VERDICT_NOTE, id="CLI verdict copy"),
        pytest.param(KEYS, id="terminal keys"),
        pytest.param(CELL_TONES, id="terminal punch tones"),
        pytest.param(WINDOWS_SCANCODES, id="Windows scan codes"),
        pytest.param(NAV_BY_SCREEN, id="navigation"),
        pytest.param(TONE_CLASSES, id="pill tones"),
        pytest.param(GAUGE_TONE_STYLES, id="gauge tones"),
        pytest.param(KIND_CLASSES, id="calendar day classes"),
        pytest.param(BASE_STYLES, id="punch classes"),
        pytest.param(LETTER_GLYPHS, id="splash glyphs"),
        pytest.param(PORTION_GLYPH, id="absence glyphs"),
        pytest.param(OFFSET_UNITS, id="date offsets"),
        pytest.param(RELATIVE_DAYS, id="relative dates"),
        pytest.param(JUMP_TARGETS, id="dashboard jumps"),
        pytest.param(CELL_GLYPHS, id="punch glyphs"),
        pytest.param(FALLBACK, id="fallback palette"),
    ],
)
def test_public_mappings_cannot_be_changed(published: object) -> None:
    assert isinstance(published, MappingProxyType)
    with pytest.raises(TypeError):
        cast("Settable", published)[object()] = object()
