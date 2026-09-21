"""Exercise just's Python recipes without running external developer commands."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

JUSTFILE = Path(__file__).resolve().parent.parent / "justfile"
pytestmark = pytest.mark.skipif(not JUSTFILE.is_file(), reason="no task file in sdist")

RECORDER = """\
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

log = Path(os.environ["TASK_LOG"])

def record(command, **options):
    if not isinstance(command, list) or not all(
        isinstance(arg, str) for arg in command
    ):
        raise AssertionError("Recipes must pass an argument list")
    if options.get("shell"):
        raise AssertionError("Recipes must not invoke a shell")
    environment = options.get("env", os.environ)
    entry = {
        "args": command,
        "profile": environment.get("HYPOTHESIS_PROFILE"),
        "delay": environment.get("FLEXI_LATE_CALLBACKS"),
    }
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\\n")
    count = len(log.read_text(encoding="utf-8").splitlines())
    return 37 if count == int(os.environ["TASK_FAIL_AT"]) else 0

def call(command, **options):
    return record(command, **options)

def run(command, **options):
    status = record(command, **options)
    if status and options.get("check"):
        raise subprocess.CalledProcessError(status, command)
    return subprocess.CompletedProcess(command, status)

subprocess.call = call
subprocess.run = run
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


@dataclass(frozen=True)
class Invocation:
    args: tuple[str, ...]
    profile: str | None
    delay: str | None

    @classmethod
    def from_json(cls, line: str) -> Invocation:
        entry = json.loads(line)
        assert isinstance(entry, dict)
        args, profile, delay = entry["args"], entry["profile"], entry["delay"]
        assert isinstance(args, list)
        assert all(isinstance(arg, str) for arg in args)
        assert profile is None or isinstance(profile, str)
        assert delay is None or isinstance(delay, str)
        return cls(tuple(args), profile, delay)


@dataclass(frozen=True)
class Tasks:
    root: Path
    executable: str

    @property
    def calls(self) -> list[Invocation]:
        log = self.root / "calls.jsonl"
        if not log.exists():
            return []
        return [
            Invocation.from_json(line)
            for line in log.read_text(encoding="utf-8").splitlines()
        ]

    def run(self, *args: str, fail_at: int = 0) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment.pop("HYPOTHESIS_PROFILE", None)
        environment.pop("FLEXI_LATE_CALLBACKS", None)
        environment.update(
            TASK_LOG=str(self.root / "calls.jsonl"), TASK_FAIL_AT=str(fail_at)
        )
        return subprocess.run(  # noqa: S603 - discovered just and controlled copied recipes
            [
                self.executable,
                "--justfile",
                str(self.root / "justfile"),
                "--working-directory",
                str(self.root),
                *args,
            ],
            cwd=self.root,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )


@pytest.fixture
def tasks(tmp_path: Path) -> Tasks:
    executable = shutil.which("just")
    if executable is None:
        pytest.skip("just is not installed")
    root = tmp_path / "tasks with spaces"
    root.mkdir()
    recorder = root / "record.py"
    recorder.write_text(RECORDER, encoding="utf-8")
    interpreter = json.dumps([sys.executable, "-I", str(recorder)], ensure_ascii=False)
    source, replaced = re.subn(
        r"(?m)^set script-interpreter := .+$",
        lambda _: f"set script-interpreter := {interpreter}",
        JUSTFILE.read_text(encoding="utf-8"),
    )
    assert replaced == 1, "the task interpreter must be replaced before any recipe runs"
    (root / "justfile").write_text(source, encoding="utf-8")
    return Tasks(root, executable)


def test_every_recipe_parses_and_default_only_lists_help(tasks: Tasks) -> None:
    parsed = tasks.run("--summary")
    assert parsed.returncode == 0, parsed.stderr
    assert {"test", "package-check", "release-prepare", "try-release"} <= set(
        parsed.stdout.split()
    )
    assert tasks.calls == []

    result = tasks.run()
    assert result.returncode == 0, result.stderr
    assert [call.args for call in tasks.calls] == [("just", "--list", "--unsorted")]


@pytest.mark.parametrize(
    "recipe",
    ["run", "test", "test-ci", "coverage", "test-late", "test-floors", "try-release"],
)
def test_variadic_arguments_reach_the_command_without_shell_expansion(
    tasks: Tasks, recipe: str
) -> None:
    arguments = (
        "path with spaces",
        "'single' and \"double\"",
        "$(touch injected)",
        "; touch injected",
        "--run",
        "123",
        "--demo",
    )
    result = tasks.run(recipe, *arguments)
    assert result.returncode == 0, result.stderr
    assert len(tasks.calls) == 1
    assert tasks.calls[0].args[-len(arguments) :] == arguments
    assert not (tasks.root / "injected").exists()


@pytest.mark.parametrize(
    ("recipe", "profile", "delay"),
    [
        ("test", None, None),
        ("test-ci", "ci", None),
        ("coverage", "ci", None),
        ("test-late", "ci", "0.05"),
    ],
)
def test_testing_recipes_select_the_intended_budget_and_callback_delay(
    tasks: Tasks, recipe: str, profile: str | None, delay: str | None
) -> None:
    result = tasks.run(recipe)
    assert result.returncode == 0, result.stderr
    assert len(tasks.calls) == 1
    assert (tasks.calls[0].profile, tasks.calls[0].delay) == (profile, delay)


def test_release_title_is_one_literal_argument(tasks: Tasks) -> None:
    version = '1.2.3 "quoted"; $(touch injected)'
    result = tasks.run("release-check", version)
    assert result.returncode == 0, result.stderr
    args = tasks.calls[0].args
    assert args[args.index("--title") + 1] == f"chore(release): {version}"
    assert "--check-snapshots" in args
    assert not (tasks.root / "injected").exists()


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_release_preparation_stops_at_the_first_failed_step(
    tasks: Tasks, fail_at: int
) -> None:
    result = tasks.run("release-prepare", "1.2.3", fail_at=fail_at)
    assert result.returncode != 0
    assert len(tasks.calls) == fail_at
    assert "prepare" in tasks.calls[0].args
    if fail_at > 1:
        assert "scripts/shoot.py" in tasks.calls[1].args
    if fail_at > 2:
        assert "check" in tasks.calls[2].args


def test_failed_setup_does_not_install_hooks(tasks: Tasks) -> None:
    result = tasks.run("setup", fail_at=1)
    assert result.returncode != 0
    assert len(tasks.calls) == 1
    assert "sync" in tasks.calls[0].args


def test_failed_test_command_fails_the_recipe(tasks: Tasks) -> None:
    result = tasks.run("test", "tests/domain", fail_at=1)
    assert result.returncode != 0
    assert len(tasks.calls) == 1
    assert tasks.calls[0].args[-1] == "tests/domain"


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows symlinks need extra privileges"
)
@pytest.mark.parametrize(
    ("recipe", "output"), [("build", "dist"), ("build-staging", "test-dist")]
)
def test_build_refuses_to_replace_a_linked_output(
    tasks: Tasks, recipe: str, output: str
) -> None:
    target = tasks.root / "valuable"
    target.mkdir()
    saved = target / "keep"
    saved.write_text("untouched", encoding="utf-8")
    (tasks.root / output).symlink_to(target, target_is_directory=True)
    result = tasks.run(recipe)
    assert result.returncode != 0
    assert "Refusing to replace a linked" in result.stderr
    assert saved.read_text(encoding="utf-8") == "untouched"
    assert len(tasks.calls) == (1 if recipe == "build-staging" else 0)
