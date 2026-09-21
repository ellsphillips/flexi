"""Test the lowest direct dependencies without changing the working checkout."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from scripts.release_probe import environment

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = frozenset(
    {
        ".git",
        ".venv",
        ".probe",
        ".tox",
        ".nox",
        ".cache",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "test-dist",
    }
)
COMMAND_TIMEOUT = 3600
# Exercise the oldest dependencies on the minimum supported Python version.
FLOOR_PYTHON = "3.12"


class FloorsError(Exception):
    """A temporary dependency-floor check could not finish."""


def checkout_files(root: Path, git: str, env: dict[str, str]) -> list[Path]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed Git operation, no shell
            [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        message = "Could not list checkout files; run this check from a Git checkout"
        raise FloorsError(message) from error
    files = []
    for name in result.stdout.split(b"\0"):
        if not name:
            continue
        path = Path(os.fsdecode(name))
        if path.root or path.drive or ".." in path.parts:
            message = "Git returned a path outside the checkout"
            raise FloorsError(message)
        files.append(path)
    return sorted(set(files))


def copy_checkout(root: Path, destination: Path, files: Sequence[Path]) -> None:
    for relative in files:
        if any(
            part in EXCLUDED or part.startswith(".venv-") for part in relative.parts
        ):
            continue
        source = root
        for part in relative.parts:
            source /= part
            if source.is_symlink() or source.is_junction():
                message = f"Cannot copy linked checkout path: {relative}"
                raise FloorsError(message)
            if not source.exists():
                # A tracked file deleted in the working tree stays deleted.
                break
            if source.is_dir() and (source / "pyvenv.cfg").is_file():
                # Custom-named virtual environments are not project source.
                break
        else:
            if not source.is_file():
                message = f"Checkout path is not a regular file: {relative}"
                raise FloorsError(message)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Copy file contents, never a link that could write into the checkout.
            shutil.copy2(source, target)


def run(command: list[str], *, root: Path, env: dict[str, str], operation: str) -> None:
    print(f"{operation}...", flush=True)
    try:
        subprocess.run(  # noqa: S603 - argument lists preserve literal pytest options
            command,
            cwd=root,
            env=env,
            check=True,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.CalledProcessError as error:
        message = f"{operation} failed (exit {error.returncode}); see the output above"
        raise FloorsError(message) from error
    except subprocess.TimeoutExpired as error:
        message = f"{operation} exceeded the one-hour limit"
        raise FloorsError(message) from error
    except OSError as error:
        message = f"Could not start {operation.lower()}; check your uv installation"
        raise FloorsError(message) from error


def check_floors(arguments: Sequence[str], *, root: Path | None = None) -> None:
    checkout = (root or ROOT).resolve()
    uv, git = shutil.which("uv"), shutil.which("git")
    if uv is None or git is None:
        message = "Install uv and Git before checking dependency floors"
        raise FloorsError(message)
    try:
        with tempfile.TemporaryDirectory(prefix="flexi-floors-") as temporary:
            workspace = Path(temporary).resolve()
            env = environment(workspace)
            env["HYPOTHESIS_PROFILE"] = "ci"
            project = workspace / "project"
            project.mkdir()
            env["UV_PROJECT_ENVIRONMENT"] = str(project / ".venv")
            copy_checkout(checkout, project, checkout_files(checkout, git, env))
            discovery_env = env.copy()
            del discovery_env["XDG_DATA_HOME"]
            try:
                result = subprocess.run(  # noqa: S603 - fixed uv query, no shell
                    [uv, "--no-config", "python", "dir"],
                    cwd=project,
                    env=discovery_env,
                    check=True,
                    capture_output=True,
                    encoding="utf-8",
                    timeout=30,
                )
            except (OSError, UnicodeError, subprocess.SubprocessError) as error:
                message = (
                    "Could not find uv's managed Python directory; "
                    "check your uv installation"
                )
                raise FloorsError(message) from error
            python_directory = result.stdout.rstrip("\r\n")
            if not Path(python_directory).is_absolute():
                message = "uv returned an invalid managed Python directory"
                raise FloorsError(message)
            env["UV_PYTHON_INSTALL_DIR"] = python_directory
            uv_command = [uv, "--no-config", "--cache-dir", str(workspace / "uv-cache")]
            python_options = ["--python", FLOOR_PYTHON]
            operations = (
                (
                    ["lock", *python_options, "--resolution", "lowest-direct"],
                    "Resolving dependency floors",
                ),
                (
                    ["sync", *python_options, "--frozen", "--dev"],
                    "Installing dependency floors",
                ),
                (
                    ["run", *python_options, "--frozen", "pytest", *arguments],
                    "Testing dependency floors",
                ),
            )
            for options, operation in operations:
                run([*uv_command, *options], root=project, env=env, operation=operation)
    except OSError as error:
        message = (
            "Could not copy or remove temporary floor-check files; "
            "close open files and retry"
        )
        raise FloorsError(message) from error


def main(argv: Sequence[str] | None = None) -> int:
    try:
        check_floors(sys.argv[1:] if argv is None else argv)
    except KeyboardInterrupt:
        print("Dependency-floor check interrupted", file=sys.stderr)
        return 130
    except FloorsError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
