"""Preferences: keybindings and defaults, from ``~/.config/flexi/config.yaml``.

Distinct from *settings*, which live in the database because the balance depends
on them. Config is preference: which key clocks in, which period opens.

Bindings read :data:`CONFIG` at class-definition time, so this module is
imported before any widget module and must import nothing from Flexi that could
import it back. :mod:`flexi.locations` and :mod:`flexi.constants` are leaves
that import nothing from Flexi at all.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from flexi.constants import AbsenceType, Granularity
from flexi.locations import config_file

__all__ = (
    "CONFIG",
    "CONFIG_PROBLEM",
    "IGNORED",
    "MAXIMUM_MINIMUM_SESSION_SECONDS",
    "MAXIMUM_TICK_SECONDS",
    "UNUSABLE",
    "Config",
    "Defaults",
    "Hotkeys",
    "load_config",
    "normalise_hotkey",
    "read_config",
    "section",
)

UNUSABLE = "Flexi could not read {path}, so none of its preferences are in force."

IGNORED = (
    "Flexi is using its own preferences: {sections} in {path} could not be used.\n{why}"
)
"""What a file that was read and then dropped says for itself.

A section falls back whole, so one misspelled key under `defaults` loses the
period the dashboard opens on as well. Unsaid, the file sits there looking as
though it is in force."""

MAXIMUM_MINIMUM_SESSION_SECONDS = 3600
"""Largest supported threshold for deciding a newly closed session was a slip."""

MAXIMUM_TICK_SECONDS = 60
"""Slowest supported live-clock refresh interval."""


_KEY_COMPONENT = re.compile(r"[A-Za-z0-9_-]+|\S")
"""One part of a chord: a Textual key name, or a single character."""


def normalise_hotkey(value: str) -> str:
    """Return a canonical comma-separated Textual key list.

    Textual treats commas and plus signs as separators, and an empty segment
    reaches ``Binding`` and raises during module import, before Flexi can draw
    an error or fall back. A key is a Textual key name or the single character
    it stands for, so `slash`, `/` and `ctrl+l` are all keys; anything longer is
    read as markup wherever the key is drawn.
    """
    bindings = tuple(binding.strip() for binding in value.split(","))
    malformed = any(
        not binding
        or not binding.isprintable()
        or any(
            _KEY_COMPONENT.fullmatch(component) is None
            for component in binding.split("+")
        )
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

    @model_validator(mode="after")
    def reject_keys_bound_twice(self) -> Hotkeys:
        """Refuse a key that two actions answer to.

        Textual gives the key to one of them and says nothing about the other,
        so the second action is unreachable and the help screen offers no clue
        which one won. The whole section falls back, as it does for a misspelled
        field name.
        """
        from textual.binding import Binding

        counted = Counter(
            binding.key
            for name in type(self).model_fields
            for binding in Binding.make_bindings(
                [Binding(str(getattr(self, name)), name)]
            )
        )
        shared = sorted(key for key, count in counted.items() if count > 1)
        if shared:
            msg = f"One key, one action: {', '.join(shared)} is bound twice"
            raise ValueError(msg)
        return self

    def book(self, kind: AbsenceType) -> str:
        """The key that books one kind of absence.

        Derived from the type, so a legend or a prompt cannot disagree with the
        binding it is describing. The field names follow the display token,
        which is why TOIL is `book_toil` while the stored value is `flexi`.
        """
        return str(getattr(self, f"book_{kind.token}"))


class Defaults(BaseModel):
    """How the application opens, and the few behaviours worth tuning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period: Granularity = Granularity.WEEK
    """Which span the dashboard opens on.

    Typed, so a misspelling in the file is a validation error `load_config`
    turns into the defaults, not a `ValueError` raised from `Granularity(...)`
    while the first screen is being built."""

    first_day_of_week: int = Field(default=0, ge=0, le=6)
    """Monday is 0. Bounded, because nothing downstream rejects a 9: the grid
    would rotate by `9 % 7` while the column headings, which are sliced and not
    rotated, would stay on Monday."""
    minimum_session_seconds: Annotated[
        int, Field(ge=0, le=MAXIMUM_MINIMUM_SESSION_SECONDS)
    ] = 60
    """A session shorter than this never happened.

    Clocking in and straight back out is a slip of the finger, not a minute of
    work. Sixty seconds is long enough to cover a double-press and short enough
    to keep the shortest real errand."""

    tick_seconds: Annotated[int, Field(gt=0, le=MAXIMUM_TICK_SECONDS)] = 1
    """How often the live readout refreshes while a session is open. A minute
    would make an elapsed clock jump in 60-second steps, which looks broken."""


class Config(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hotkeys: Hotkeys = Field(default_factory=Hotkeys)
    defaults: Defaults = Field(default_factory=Defaults)


def load_config(path: Path | None = None) -> Config:
    """The preferences, with anything the file got wrong replaced by a default.

    :func:`read_config` gives the same answer with the reason attached.
    """
    return read_config(path)[0]


def read_config(path: Path | None = None) -> tuple[Config, str]:
    """The preferences, and one line about whatever in the file was ignored.

    A malformed file yields the defaults. `CONFIG` is bound at module scope, so
    anything that escapes here is `import flexi.config` raising, which takes
    `flexi --version` down with it; a document nested past the interpreter's
    recursion limit is malformed in that way, hence `RecursionError`.

    PyYAML is handed the bytes, not a decoded string, so a byte order mark
    chooses the encoding: PowerShell's `>` writes UTF-16 without being asked.
    Bytes that name no encoding are UTF-8, and no other sniffing is done.

    Sections are validated one at a time: `extra="forbid"` makes an unknown key
    an error, and one under `defaults` would otherwise discard the hotkeys too.
    """
    path = path or config_file()
    try:
        raw: object = yaml.safe_load(path.read_bytes())
    except FileNotFoundError:
        # No file at all is no preference, and the commonest run of all.
        return Config(), ""
    except (OSError, yaml.YAMLError, RecursionError, ValueError):
        return Config(), UNUSABLE.format(path=path)
    if raw is None:
        # An empty file, which says nothing and gets nothing wrong.
        return Config(), ""
    if not isinstance(raw, dict):
        return Config(), UNUSABLE.format(path=path)

    hotkeys, hotkeys_why = section(Hotkeys, raw.get("hotkeys"))
    defaults, defaults_why = section(Defaults, raw.get("defaults"))
    dropped = {"hotkeys": hotkeys_why, "defaults": defaults_why}
    unknown = [ascii(name) for name in raw if name not in Config.model_fields]
    if unknown:
        dropped["unknown sections"] = f"Unrecognised sections: {', '.join(unknown)}."
    named = [name for name, why in dropped.items() if why]
    problem = (
        IGNORED.format(
            sections=" and ".join(named),
            path=path,
            why=next(why for why in dropped.values() if why),
        )
        if named
        else ""
    )
    return Config(hotkeys=hotkeys, defaults=defaults), problem


def section[T: BaseModel](model: type[T], raw: object) -> tuple[T, str]:
    """One section of the file, and why it was dropped if it was.

    The reason is empty when the section was taken as written, and when the
    file does not mention it.
    """
    if raw is None:
        return model(), ""
    if not isinstance(raw, dict):
        return model(), "Expected a mapping of preference names to values."
    try:
        return model.model_validate(raw), ""
    except ValidationError as invalid:
        return model(), _first_complaint(invalid)


def _first_complaint(invalid: ValidationError) -> str:
    """The first thing pydantic objected to, as a phrase the user can act on.

    One, not all of them: the section falls back whole either way, and the line
    has to fit on a status bar and in a toast.
    """
    first = invalid.errors()[0]
    field = ".".join(str(part) for part in first["loc"])
    said = str(first["msg"]).removeprefix("Value error, ")
    return f"{field}: {said}." if field else f"{said}."


CONFIG, CONFIG_PROBLEM = read_config()
"""The loaded config. Read at class-definition time by every ``BINDINGS`` list.

Bound as a pair so the reason travels with the answer, and read once so every
caller sees the same answer."""
