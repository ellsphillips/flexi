"""Where Flexi puts things, on each platform, and what it creates by asking.

The second half matters as much as the first: these functions answer a
question, and a question should not have a filesystem side effect. Every one of
them used to call mkdir, so `flexi --version` left a config directory behind on
a machine that had never run the application.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from flexi import locations

XDG = ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "LOCALAPPDATA", "APPDATA")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in XDG:
        monkeypatch.delenv(name, raising=False)


def test_xdg_data_home_wins_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert locations.database_file() == tmp_path / "flexi" / "db.db"


def test_xdg_config_home_wins_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert locations.config_file() == tmp_path / "flexi" / "config.yaml"


def test_xdg_is_honoured_on_windows_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Somebody who sets XDG_DATA_HOME on Windows means it."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("XDG_DATA_HOME", str(Path("D:/data").resolve()))
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/x/AppData/Local")
    assert locations.data_home() == Path("D:/data").resolve()


@pytest.mark.parametrize("value", ["", "   ", "relative/path", "./here"])
def test_a_relative_or_empty_setting_is_ignored(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Otherwise XDG_DATA_HOME=. drops a database wherever you were standing.

    Compared against the answer with nothing set, rather than against
    `~/.local/share`. What is being asserted is that the value was ignored, and
    naming the POSIX default as well made this the one test in the file that
    failed on Windows -- where the fallback is `%LOCALAPPDATA%`, as the tests
    below say it should be.
    """
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    unset = locations.data_home()

    monkeypatch.setenv("XDG_DATA_HOME", value)

    assert locations.data_home() == unset


def test_windows_uses_localappdata_for_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(Path("C:/Users/x/AppData/Local").resolve()))
    assert locations.data_home() == Path("C:/Users/x/AppData/Local").resolve()


def test_windows_uses_appdata_for_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(Path("C:/Users/x/AppData/Roaming").resolve()))
    assert locations.config_home() == Path("C:/Users/x/AppData/Roaming").resolve()


def test_windows_without_the_variables_still_lands_somewhere_sensible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert locations.data_home() == Path.home() / "AppData" / "Local"
    assert locations.config_home() == Path.home() / "AppData" / "Roaming"


@pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd"])
def test_everywhere_else_is_xdg_by_default(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    assert locations.data_home() == Path.home() / ".local" / "share"
    assert locations.config_home() == Path.home() / ".config"


@pytest.mark.parametrize(
    "ask",
    [
        locations.data_home,
        locations.config_home,
        locations.data_directory,
        locations.config_directory,
        locations.config_file,
        locations.database_file,
        locations.backups_directory,
    ],
)
def test_asking_where_something_lives_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ask: object
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    ask()  # type: ignore[operator]

    assert list(tmp_path.iterdir()) == []


def test_ensure_is_how_a_directory_gets_made(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested"
    assert locations.ensure(target) == target
    assert target.is_dir()
    assert locations.ensure(target) == target  # idempotent
