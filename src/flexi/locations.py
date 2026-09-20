"""Where Flexi keeps its database and its preferences, on any operating system.

``XDG_DATA_HOME`` and ``XDG_CONFIG_HOME`` win when set, on every platform.
Otherwise ``%LOCALAPPDATA%`` and ``%APPDATA%`` on Windows, ``~/.local/share``
and ``~/.config`` elsewhere.

Nothing here creates a directory: asking where a file lives puts nothing on
disk. Writers call :func:`ensure` at the point they write.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = (
    "APP_NAME",
    "BACKUPS_DIRNAME",
    "CONFIG_FILENAME",
    "DATABASE_FILENAME",
    "absolute_from_env",
    "backups_directory",
    "config_directory",
    "config_file",
    "config_home",
    "data_directory",
    "data_home",
    "database_file",
    "ensure",
)

APP_NAME = "flexi"
CONFIG_FILENAME = "config.yaml"
DATABASE_FILENAME = "db.db"
BACKUPS_DIRNAME = "backups"


def absolute_from_env(variable: str) -> Path | None:
    """Return an absolute path from the environment, or ``None``.

    A relative value is ignored, as the XDG specification asks: a stray
    ``XDG_DATA_HOME=.`` would leave databases wherever the shell happened to
    be standing.
    """
    raw = os.environ.get(variable, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else None


def data_home() -> Path:
    """The root this machine puts application data under."""
    if (configured := absolute_from_env("XDG_DATA_HOME")) is not None:
        return configured
    if sys.platform == "win32":
        local = absolute_from_env("LOCALAPPDATA")
        return local if local is not None else Path.home() / "AppData" / "Local"
    return Path.home() / ".local" / "share"


def config_home() -> Path:
    """The root this machine puts application preferences under."""
    if (configured := absolute_from_env("XDG_CONFIG_HOME")) is not None:
        return configured
    if sys.platform == "win32":
        roaming = absolute_from_env("APPDATA")
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
    """Create a private directory without changing an existing custom parent.

    Flexi's dedicated directories are kept private on subsequent runs. A
    caller-supplied database may live in a shared directory, so existing custom
    parents and directory symlinks retain their permissions.
    """
    try:
        directory.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        if not directory.is_dir():
            raise
        if directory.is_symlink() or directory not in (
            data_directory(),
            config_directory(),
            backups_directory(),
        ):
            return directory
    directory.chmod(0o700)
    return directory
