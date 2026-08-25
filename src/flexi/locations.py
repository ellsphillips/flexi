"""Where Flexi keeps its database and its preferences, on any operating system.

XDG first. ``XDG_DATA_HOME`` and ``XDG_CONFIG_HOME`` are honoured wherever they
are set, including on Windows, because somebody who sets them means it.
Otherwise the platform's own convention: ``%LOCALAPPDATA%`` and ``%APPDATA%`` on
Windows, ``~/.local/share`` and ``~/.config`` everywhere else.

Nothing here creates a directory. Asking where a file lives should not put
anything on disk -- ``flexi --version`` used to leave a config directory behind
on a machine that had never run the application. Writers call :func:`ensure` at
the point they write.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "flexi"
CONFIG_FILENAME = "config.yaml"
DATABASE_FILENAME = "db.db"
BACKUPS_DIRNAME = "backups"


def _absolute_from_env(variable: str) -> Path | None:
    """An absolute path from the environment, or ``None``.

    A relative value is ignored rather than resolved against the working
    directory. That is what the XDG specification asks for, and it stops a
    stray ``XDG_DATA_HOME=.`` leaving databases wherever you happened to be
    standing.
    """
    raw = os.environ.get(variable, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else None


def data_home() -> Path:
    """The root this machine puts application data under."""
    if (configured := _absolute_from_env("XDG_DATA_HOME")) is not None:
        return configured
    if sys.platform == "win32":
        local = _absolute_from_env("LOCALAPPDATA")
        return local if local is not None else Path.home() / "AppData" / "Local"
    return Path.home() / ".local" / "share"


def config_home() -> Path:
    """The root this machine puts application preferences under."""
    if (configured := _absolute_from_env("XDG_CONFIG_HOME")) is not None:
        return configured
    if sys.platform == "win32":
        roaming = _absolute_from_env("APPDATA")
        return roaming if roaming is not None else Path.home() / "AppData" / "Roaming"
    return Path.home() / ".config"


def data_directory() -> Path:
    return data_home() / APP_NAME


def config_directory() -> Path:
    return config_home() / APP_NAME


def config_file() -> Path:
    return config_directory() / CONFIG_FILENAME


def database_file() -> Path:
    return data_directory() / DATABASE_FILENAME


def backups_directory() -> Path:
    return data_directory() / BACKUPS_DIRNAME


def ensure(directory: Path) -> Path:
    """Create a directory and return it, for the moment before a write."""
    directory.mkdir(parents=True, exist_ok=True)
    return directory
