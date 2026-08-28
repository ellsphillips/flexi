"""Preferences: keybindings and defaults, from ``~/.config/flexi/config.yaml``.

Distinct from *settings*, which live in the database because the balance depends
on them. Config is preference: which key clocks in, which period opens.

Bindings read :data:`CONFIG` at class-definition time, so this module is
imported before any widget module and must import nothing from Flexi that could
import it back. :mod:`flexi.locations` and :mod:`flexi.constants` are the two it
may reach for: both are leaves that import nothing from Flexi at all, which is
the property that matters rather than the count.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from flexi.constants import AbsenceType, Granularity
from flexi.locations import config_file

__all__ = (
    "CONFIG",
    "MAXIMUM_MINIMUM_SESSION_SECONDS",
    "MAXIMUM_TICK_SECONDS",
    "Config",
    "Defaults",
    "Hotkeys",
    "load_config",
    "normalise_hotkey",
    "section",
)

MAXIMUM_MINIMUM_SESSION_SECONDS = 3600
"""Largest supported threshold for deciding a newly closed session was a slip."""

MAXIMUM_TICK_SECONDS = 60
"""Slowest supported live-clock refresh interval."""


def normalise_hotkey(value: str) -> str:
    """Return a canonical comma-separated Textual key list.

    Textual treats commas and plus signs as separators. Empty segments reach
    ``Binding`` and raise during module import, before Flexi can draw an error
    or fall back. Outer whitespace is harmless and removed; whitespace inside
    a key name and missing chord components are rejected as malformed config.
    """
    bindings = tuple(binding.strip() for binding in value.split(","))
    malformed = any(
        not binding
        or any(character.isspace() for character in binding)
        or any(not modifier for modifier in binding.split("+"))
        for binding in bindings
    )
    if malformed:
        msg = "A hotkey must contain one or more complete key names"
        raise ValueError(msg)
    return ",".join(bindings)


class Hotkeys(BaseModel):
    """Every binding, in one place. See ``docs/KEYMAP.md``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # global
    clock_toggle: str = "slash"
    toggle_jump_mode: str = "v"
    help: str = "question_mark"
    log: str = "ctrl+l"

    # period
    period_day: str = "d"
    period_week: str = "w"
    period_month: str = "m"
    period_year: str = "y"
    period_cycle: str = "p"
    period_prev: str = "left_square_bracket"
    period_next: str = "right_square_bracket"
    today: str = "t"
    go_to_date: str = "g"

    # records
    expand: str = "space"
    expand_all: str = "shift+space"
    new_session: str = "n"
    corrections: str = "N"
    edit: str = "e"
    delete: str = "x"

    # absence, from anywhere on the dashboard
    book_annual: str = "A"
    book_sick: str = "S"
    book_toil: str = "T"
    book_unpaid: str = "U"
    book_other: str = "O"
    book_absence: str = "a"

    @field_validator("*")
    @classmethod
    def normalise_bindings(cls, value: str) -> str:
        """Validate every binding before Textual imports it."""
        return normalise_hotkey(value)

    def book(self, kind: AbsenceType) -> str:
        """The key that books one kind of absence.

        Derived from the type rather than restated beside it, so a legend or a
        prompt cannot disagree with the binding it is describing. The field
        names follow the display token, which is why TOIL is `book_toil` while
        the stored value is `flexi`.
        """
        return str(getattr(self, f"book_{kind.token}"))


class Defaults(BaseModel):
    """How the application opens, and the few behaviours worth tuning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period: Granularity = Granularity.WEEK
    """Which span the dashboard opens on.

    Typed, so a misspelling in the file is a validation error `load_config`
    turns into the defaults. As a bare `str` it reached `Granularity(...)` in
    the dashboard's constructor and raised there instead -- a `ValueError`
    thrown while building the first screen, which is a preference typo taking
    the application down."""

    first_day_of_week: int = Field(default=0, ge=0, le=6)
    """Monday is 0. Bounded, because nothing downstream rejects a 9: the grid
    would rotate by `9 % 7` while the column headings, sliced rather than
    rotated, would silently stay on Monday."""
    minimum_session_seconds: Annotated[
        int, Field(ge=0, le=MAXIMUM_MINIMUM_SESSION_SECONDS)
    ] = 60
    """A session shorter than this never happened.

    Clocking in and straight back out is a slip of the finger, not a minute of
    work, and a records table full of them is a records table nobody trusts.
    Sixty seconds is long enough to cover a double-press and short enough that
    nobody loses a real errand to it."""

    tick_seconds: Annotated[int, Field(gt=0, le=MAXIMUM_TICK_SECONDS)] = 1
    """How often the live readout refreshes while a session is open. A minute
    would make an elapsed clock jump in 60-second steps, which looks broken."""


class Config(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hotkeys: Hotkeys = Field(default_factory=Hotkeys)
    defaults: Defaults = Field(default_factory=Defaults)


def load_config(path: Path | None = None) -> Config:
    """Read the config file, falling back to defaults section by section.

    A malformed file yields the defaults rather than refusing to start: a typo
    in a keybinding should not lock somebody out of their own time records.

    Section by section, though, and not wholesale. Validated as one document, a
    single unknown key under `defaults` -- and `extra="forbid"` makes an unknown
    key an error -- threw away the hotkeys too, silently. The documented example
    contained two such keys, so somebody who copied it from `ARCHITECTURE.md`
    got every default back and no way to tell why.
    """
    path = path or config_file()
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return Config()
    if not isinstance(raw, dict):
        return Config()
    return Config(
        hotkeys=section(Hotkeys, raw.get("hotkeys")),
        defaults=section(Defaults, raw.get("defaults")),
    )


def section[T: BaseModel](model: type[T], raw: object) -> T:
    """One section of the file, or that section's defaults."""
    if not isinstance(raw, dict):
        return model()
    try:
        return model.model_validate(raw)
    except ValidationError:
        return model()


CONFIG: Config = load_config()
"""The loaded config. Read at class-definition time by every ``BINDINGS`` list."""
