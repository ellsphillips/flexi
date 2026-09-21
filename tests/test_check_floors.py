"""Dependency-floor checks preserve the real checkout and literal arguments."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from scripts import check_floors as floors


@dataclass(frozen=True)
class Invocation:
    command: list[str]
    cwd: Path
    env: dict[str, str]


class Runner:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths = [
            "pyproject.toml",
            "uv.lock",
            "src/changed.py",
            "new file.py",
            ".github/workflows/tests.yaml",
            "deleted.py",
            ".venv/sentinel",
            ".git/config",
            "dist/stale.whl",
            "custom-env/pyvenv.cfg",
            "custom-env/bin/python",
        ]
        self.calls: list[Invocation] = []
        self.fail_at: int | None = None
        self.git_failure = False
        self.lookup_failure = False
        self.python_directory = str(root.parent / "shared Python installations")

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        timeout: int,
        capture_output: bool = False,
        encoding: str | None = None,
    ) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
        assert check
        self.calls.append(Invocation(command, cwd, dict(env)))
        if command[0] == "git-test":
            assert cwd == self.root
            assert timeout == 30
            assert capture_output
            if self.git_failure:
                raise subprocess.CalledProcessError(128, command)
            assert command[1:] == [
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ]
            paths = b"\0".join(os.fsencode(path) for path in self.paths) + b"\0"
            return subprocess.CompletedProcess(command, 0, paths, b"")
        if command == ["uv-test", "--no-config", "python", "dir"]:
            assert timeout == 30
            assert capture_output
            assert encoding == "utf-8"
            assert "XDG_DATA_HOME" not in env
            assert "UV_PYTHON_INSTALL_DIR" not in env
            if self.lookup_failure:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(
                command, 0, self.python_directory + "\n", ""
            )
        assert not capture_output
        assert timeout == floors.COMMAND_TIMEOUT
        assert cwd != self.root
        assert cwd.is_dir()
        assert (cwd / "src/changed.py").read_text() == "uncommitted edit"
        assert (cwd / "new file.py").read_text() == "untracked source"
        assert (cwd / ".github/workflows/tests.yaml").read_text() == "workflow"
        assert not (cwd / "deleted.py").exists()
        assert not (cwd / ".git").exists()
        assert not (cwd / "dist").exists()
        assert not (cwd / "custom-env").exists()
        if "lock" in command:
            assert (cwd / "uv.lock").read_text() == "original lock"
            assert not (cwd / ".venv").exists()
            (cwd / "uv.lock").write_text("lowest lock")
        else:
            assert (cwd / "uv.lock").read_text() == "lowest lock"
        if "sync" in command:
            (cwd / ".venv").mkdir()
            (cwd / ".venv/sentinel").write_text("lowest environment")
        if len(self.calls) - 2 == self.fail_at:
            raise subprocess.CalledProcessError(2, command)
        return subprocess.CompletedProcess(command, 0, b"", b"")


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runner:
    root = tmp_path / "checkout with spaces"
    contents = {
        "pyproject.toml": "[project]",
        "uv.lock": "original lock",
        "src/changed.py": "uncommitted edit",
        "new file.py": "untracked source",
        ".github/workflows/tests.yaml": "workflow",
        ".venv/sentinel": "original environment",
        ".git/config": "git metadata",
        "dist/stale.whl": "stale artifact",
        "custom-env/pyvenv.cfg": "home = ignored",
        "custom-env/bin/python": "ignored",
    }
    for name, content in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    runner = Runner(root)
    monkeypatch.setattr(subprocess, "run", runner.run)
    monkeypatch.setattr(shutil, "which", lambda name: f"{name}-test")
    return runner


def assert_checkout_preserved(runner: Runner) -> None:
    assert (runner.root / "uv.lock").read_text() == "original lock"
    assert (runner.root / ".venv/sentinel").read_text() == "original environment"
    assert not (runner.root / "deleted.py").exists()
    assert runner.calls[0].env["XDG_DATA_HOME"] != str(runner.root)
    assert not Path(runner.calls[0].env["XDG_DATA_HOME"]).parent.exists()


def test_floor_check_uses_an_isolated_current_checkout_and_exact_arguments(
    runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON_INSTALL_DIR",
        "UV_INDEX_URL",
        "PIP_INDEX_URL",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GH_TOKEN",
        "HYPOTHESIS_PROFILE",
    ):
        monkeypatch.setenv(key, "must-not-travel")
    arguments = ["tests/name with spaces.py", "-k", "one or two", "'quote'", "$(cmd);x"]

    floors.check_floors(arguments, root=runner.root)

    assert len(runner.calls) == 5
    for call in runner.calls:
        assert "must-not-travel" not in call.env.values()
        assert call.env["HYPOTHESIS_PROFILE"] == "ci"
        assert call.env["NETRC"] == os.devnull
    for call in runner.calls[2:]:
        assert call.command[:2] == ["uv-test", "--no-config"]
        assert Path(call.env["UV_PROJECT_ENVIRONMENT"]) == call.cwd / ".venv"
        assert call.env["UV_PYTHON_INSTALL_DIR"] == runner.python_directory
    python = ["--python", "3.12"]
    assert runner.calls[2].command[4:] == [
        "lock",
        *python,
        "--resolution",
        "lowest-direct",
    ]
    assert runner.calls[3].command[4:] == ["sync", *python, "--frozen", "--dev"]
    assert runner.calls[4].command[4:] == [
        "run",
        *python,
        "--frozen",
        "pytest",
        *arguments,
    ]
    assert_checkout_preserved(runner)


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_failed_floor_operation_stops_and_cleans_up(
    runner: Runner, failure: int
) -> None:
    runner.fail_at = failure
    with pytest.raises(floors.FloorsError, match="failed \\(exit 2\\)"):
        floors.check_floors([], root=runner.root)
    assert len(runner.calls) == failure + 2
    assert_checkout_preserved(runner)


def test_git_failure_stops_before_copy_or_dependency_resolution(runner: Runner) -> None:
    runner.git_failure = True
    with pytest.raises(floors.FloorsError, match="Git checkout"):
        floors.check_floors([], root=runner.root)
    assert len(runner.calls) == 1
    assert_checkout_preserved(runner)


def test_python_directory_lookup_failure_stops_and_cleans_up(runner: Runner) -> None:
    runner.lookup_failure = True
    with pytest.raises(floors.FloorsError, match="find uv's managed Python directory"):
        floors.check_floors([], root=runner.root)
    assert len(runner.calls) == 2
    assert_checkout_preserved(runner)


def test_python_directory_must_be_absolute(runner: Runner) -> None:
    runner.python_directory = "relative Python directory"
    with pytest.raises(floors.FloorsError, match="invalid managed Python directory"):
        floors.check_floors([], root=runner.root)
    assert len(runner.calls) == 2
    assert_checkout_preserved(runner)


@pytest.mark.parametrize("path", ["../uv.lock", "/outside/uv.lock"])
def test_git_paths_cannot_escape_checkout(runner: Runner, path: str) -> None:
    runner.paths = [path]
    with pytest.raises(floors.FloorsError, match="outside the checkout"):
        floors.check_floors([], root=runner.root)
    assert len(runner.calls) == 1
    assert_checkout_preserved(runner)


@pytest.mark.parametrize("linked", ["uv.lock", "src"])
def test_linked_files_and_parent_directories_are_refused(
    runner: Runner, monkeypatch: pytest.MonkeyPatch, linked: str
) -> None:
    original = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda path: path == runner.root / linked or original(path)
    )
    with pytest.raises(floors.FloorsError, match="linked checkout path"):
        floors.check_floors([], root=runner.root)
    assert len(runner.calls) == 1
    assert_checkout_preserved(runner)


def test_windows_junction_ancestors_are_refused(
    runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.is_junction
    monkeypatch.setattr(
        Path, "is_junction", lambda path: path == runner.root / "src" or original(path)
    )
    with pytest.raises(floors.FloorsError, match="linked checkout path"):
        floors.check_floors([], root=runner.root)
    assert len(runner.calls) == 1
    assert_checkout_preserved(runner)


def test_main_forwards_cli_arguments_and_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    received: list[str] = []

    def fail(arguments: Sequence[str], *, root: Path | None = None) -> None:
        received.extend(arguments)
        message = "floor resolution failed"
        raise floors.FloorsError(message)

    monkeypatch.setattr(floors, "check_floors", fail)
    assert floors.main(["--maxfail=1", "tests/space name.py"]) == 1
    assert received == ["--maxfail=1", "tests/space name.py"]
    assert capsys.readouterr().err.strip() == "floor resolution failed"
