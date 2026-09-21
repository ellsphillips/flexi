"""Local package checks exercise isolated installs and clean up on failure."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import pytest
from scripts import build_staging, release_probe
from scripts import check_package as package
from scripts.release_status import Registry

VERSION = "1.2.3"


def artifacts(
    directory: Path, registry: Registry, payload: bytes = b"application"
) -> None:
    directory.mkdir()
    name = registry.package.replace("-", "_")
    with ZipFile(directory / f"{name}-{VERSION}-py3-none-any.whl", "w") as wheel:
        wheel.writestr("flexi/__init__.py", payload)
        wheel.writestr(
            f"{name}-{VERSION}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: {registry.package}\nVersion: {VERSION}\n\n",
        )
    (directory / f"{name}-{VERSION}.tar.gz").write_bytes(b"source archive")


@dataclass(frozen=True)
class Invocation:
    command: list[str]
    cwd: Path
    env: dict[str, str]


class Commands:
    """Stand in for external tools; leave validation and orchestration real."""

    def __init__(self) -> None:
        self.calls: list[Invocation] = []
        self.staging_sources: list[Path] = []
        self.staging_payload = b"application"
        self.fail_at = ""

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        encoding: str,
        errors: str,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd.is_dir()
        self.calls.append(Invocation(command, cwd, dict(env)))
        if self.fail_at and self.fail_at in command:
            raise subprocess.CalledProcessError(1, command, stderr="tool failure")
        if "build" in command:
            artifacts(Path(command[command.index("--out-dir") + 1]), Registry.PYPI)
        if "export" in command:
            Path(command[command.index("--output-file") + 1]).write_text(
                "pytest==9.1.1\n", encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    def staging(self, source: Path, output: Path) -> str:
        assert (source / f"flexi-{VERSION}.tar.gz").read_bytes() == b"source archive"
        self.staging_sources.append(source)
        artifacts(output, Registry.TESTPYPI, self.staging_payload)
        return VERSION


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Commands:
    commands = Commands()
    monkeypatch.setattr(subprocess, "run", commands.run)
    monkeypatch.setattr(shutil, "which", lambda _name: str(tmp_path / "uv"))
    monkeypatch.setattr(build_staging, "build_staging", commands.staging)
    return commands


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project with spaces"
    root.mkdir()
    for name in ("dist", "test-dist", ".probe", ".venv"):
        directory = root / name
        directory.mkdir()
        (directory / "keep").write_text("user data", encoding="utf-8")
    (root / "uv.lock").write_text("unchanged lock", encoding="utf-8")
    return root


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_both_built_wheels_are_checked_without_touching_the_checkout(
    commands: Commands, project: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    for key in (
        "GH_TOKEN",
        "UV_INDEX",
        "PIP_EXTRA_INDEX_URL",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    ):
        monkeypatch.setenv(key, "must not travel")
    before = {
        path.relative_to(project): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }

    assert package.check_package(project) == VERSION

    assert before == {
        path.relative_to(project): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }
    build = next(call for call in commands.calls if "build" in call.command)
    scratch = build.cwd
    assert str(project) in build.command
    assert "--no-create-gitignore" in build.command
    assert "--no-sources" in build.command
    assert commands.staging_sources == [scratch / "dist"]
    assert not scratch.exists()
    twine = next(call.command for call in commands.calls if "twine" in call.command)
    assert "--strict" in twine
    assert {
        Path(value).name for value in twine if value.endswith((".whl", ".tar.gz"))
    } == {
        f"{name}-{VERSION}{suffix}"
        for name in ("flexi", "flexi_test")
        for suffix in ("-py3-none-any.whl", ".tar.gz")
    }
    export = next(call.command for call in commands.calls if "export" in call.command)
    assert {"--locked", "--no-emit-project", "--no-hashes", "--group", "dev"} <= set(
        export
    )
    tests = [call for call in commands.calls if "pytest" in call.command]
    assert {call.cwd for call in tests} == {scratch / "dist", scratch / "test-dist"}
    for call in tests:
        interpreter = (
            call.cwd
            / "venv"
            / ("Scripts/python.exe" if platform == "win32" else "bin/python")
        )
        assert call.command[:4] == [str(interpreter), "-I", "-m", "pytest"]
        assert "--noconftest" in call.command
        assert "--import-mode=importlib" in call.command
        assert call.command[call.command.index("-c") + 1] == str(
            call.cwd / "pytest.ini"
        )
        assert str(project / "tests" / "test_packaging.py") in call.command
        installs = [
            other.command
            for other in commands.calls
            if other.cwd == call.cwd and "install" in other.command
        ]
        assert any("-r" in command for command in installs)
        assert any("--no-deps" in command for command in installs)
        assert all(
            command[command.index("--python") + 1] == str(interpreter)
            for command in installs
        )
    for call in commands.calls:
        assert call.cwd.is_relative_to(scratch)
        assert all(value != "must not travel" for value in call.env.values())
        assert "publish" not in call.command
        assert not any("test.pypi.org" in argument for argument in call.command)


@pytest.mark.parametrize("failed_command", ["build", "twine", "export", "pytest"])
def test_tool_failure_is_reported_and_all_temporary_files_are_removed(
    commands: Commands, project: Path, failed_command: str
) -> None:
    commands.fail_at = failed_command
    with pytest.raises(release_probe.ProbeError, match="tool failure"):
        package.check_package(project)
    assert not commands.calls[0].cwd.exists()
    assert (project / ".venv" / "keep").read_text(encoding="utf-8") == "user data"


def test_changed_application_payload_is_refused_before_installation(
    commands: Commands, project: Path
) -> None:
    commands.staging_payload = b"changed application"
    with pytest.raises(build_staging.BuildError, match="payload"):
        package.check_package(project)
    assert not any("install" in call.command for call in commands.calls)
    assert not commands.calls[0].cwd.exists()


def test_missing_uv_has_an_actionable_error(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(release_probe.ProbeError, match="Install uv"):
        package.check_package(project)


def test_main_reports_success(
    commands: Commands,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(package, "ROOT", project)
    assert package.main([]) == 0
    assert f"Passed: flexi and flexi-test {VERSION}" in capsys.readouterr().out


def test_main_returns_failure_with_diagnostics(
    commands: Commands,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(package, "ROOT", project)
    commands.fail_at = "pytest"
    assert package.main([]) == 1
    assert "Package check failed:" in capsys.readouterr().err
    assert not commands.calls[0].cwd.exists()


def test_help_does_not_build_packages(commands: Commands) -> None:
    with pytest.raises(SystemExit) as outcome:
        package.main(["--help"])
    assert outcome.value.code == 0
    assert commands.calls == []
