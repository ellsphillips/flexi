"""Preferences: keybindings and defaults, from ``~/.config/flexi/config.yaml``.

Distinct from *settings*, which live in the database because the balance depends
on them. Config is preference: which key clocks in, which period opens.

Bindings read :data:`CONFIG` at class-definition time, so this module is
imported before any widget module and must import nothing from Flexi except
:mod:`flexi.locations`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from flexi.locations import config_file


class Hotkeys(BaseModel):
    """Every binding, in one place. See ``docs/KEYMAP.md``."""

    model_config = ConfigDict(extra="forbid")

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
    edit: str = "e"
    delete: str = "x"

    # absence, from anywhere on the dashboard
    book_annual: str = "A"
    book_sick: str = "S"
    book_toil: str = "T"
    book_unpaid: str = "U"
    book_other: str = "O"
    book_absence: str = "a"


class Defaults(BaseModel):
    """How the application opens, and the few behaviours worth tuning."""

    model_config = ConfigDict(extra="forbid")

    period: str = "week"
    first_day_of_week: int = 0
    minimum_session_seconds: int = 60
    """A session shorter than this never happened.

    Clocking in and straight back out is a slip of the finger, not a minute of
    work, and a records table full of them is a records table nobody trusts.
    Sixty seconds is long enough to cover a double-press and short enough that
    nobody loses a real errand to it."""

    tick_seconds: int = 1
    """How often the live readout refreshes while a session is open. A minute
    would make an elapsed clock jump in 60-second steps, which looks broken."""


class Config(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra="forbid")

    hotkeys: Hotkeys = Field(default_factory=Hotkeys)
    defaults: Defaults = Field(default_factory=Defaults)


def load_config(path: Path | None = None) -> Config:
    """Read the config file, falling back to defaults.

    A malformed file yields the defaults rather than refusing to start: a typo in
    a keybinding should not lock somebody out of their own time records. The
    application reports it on the status bar once it has a status bar to report
    it on.
    """
    path = path or config_file()
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return Config()
    if not isinstance(raw, dict):
        return Config()
    try:
        return Config.model_validate(raw)
    except Exception:  # noqa: BLE001 - pydantic raises a family of errors
        return Config()


def write_config(config: Config, path: Path | None = None) -> None:
    """Write the config back, creating the directory if it is missing."""
    path = path or config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config.model_dump(), sort_keys=False), encoding="utf-8"
    )


CONFIG: Config = load_config()
"""The loaded config. Read at class-definition time by every ``BINDINGS`` list."""
