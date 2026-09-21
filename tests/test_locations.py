"""Where Flexi puts things, on each platform, and what asking creates.

These functions answer a question, and a question has no filesystem side
effect: only `ensure` makes a directory.
"""

from __future__ import annotations

import stat
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
    """``XDG_DATA_HOME`` wins on every platform, Windows included."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("XDG_DATA_HOME", str(Path("D:/data").resolve()))
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/x/AppData/Local")
    assert locations.data_home() == Path("D:/data").resolve()


@pytest.mark.parametrize("value", ["", "   ", "relative/path", "./here"])
def test_relative_or_empty_setting_is_ignored(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """``XDG_DATA_HOME=.`` would otherwise drop a database in the shell's cwd.

    Compared against the answer with nothing set, not against `~/.local/share`:
    the claim is that the value is ignored, and Windows falls back elsewhere.
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


def test_windows_without_the_variables_still_lands(
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


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX modes")
def test_directories_flexi_makes_are_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New directories and Flexi's dedicated data directory remain private."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fresh = tmp_path / "fresh"
    already = locations.data_directory()
    already.mkdir()
    already.chmod(0o755)

    assert stat.S_IMODE(locations.ensure(fresh).stat().st_mode) == 0o700
    assert stat.S_IMODE(locations.ensure(already).stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX modes")
def test_existing_custom_database_parent_keeps_its_permissions(tmp_path: Path) -> None:
    from flexi.models.database.migrate import run_migrations

    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)

    run_migrations(shared / "custom.db")

    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert (shared / "custom.db").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX modes")
def test_dedicated_directory_symlink_does_not_chmod_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    linked = locations.data_directory()
    linked.symlink_to(shared, target_is_directory=True)

    assert locations.ensure(linked) == linked
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755


def test_ensure_rejects_an_existing_regular_file(tmp_path: Path) -> None:
    existing = tmp_path / "file"
    existing.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        locations.ensure(existing)

    assert existing.read_text(encoding="utf-8") == "keep"
