"""Build and exercise both distributions without publishing or retaining installs."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from zipfile import BadZipFile

from scripts import build_staging, release_probe
from scripts.release_status import Registry

ROOT = Path(__file__).resolve().parent.parent
INDEX = "https://pypi.org/simple/"


def test_installed_package(
    python: Path,
    wheel: Path,
    requirements: Path,
    project: Path,
    *,
    uv: str,
    env: dict[str, str],
) -> None:
    """Add the locked test tools while keeping the wheel as the installed project."""
    root = wheel.parent
    config = root / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    uv_command = [uv, "--no-config", "--cache-dir", str(root / "uv-cache")]
    commands = (
        (
            [
                *uv_command,
                "pip",
                "install",
                "--python",
                str(python),
                "--index-url",
                INDEX,
                "--keyring-provider",
                "disabled",
                "-r",
                str(requirements),
            ],
            "Installing locked test dependencies",
        ),
        (
            [
                *uv_command,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                str(wheel),
            ],
            "Confirming the built wheel is installed",
        ),
        (
            [*uv_command, "pip", "check", "--python", str(python)],
            "Checking the locked installation",
        ),
        (
            [
                str(python),
                "-I",
                "-m",
                "pytest",
                "-c",
                str(config),
                "--rootdir",
                str(root),
                "--noconftest",
                "--import-mode=importlib",
                "--timeout=120",
                str(project / "tests" / "test_packaging.py"),
                "-q",
            ],
            "Testing the installed package files",
        ),
    )
    for command, operation in commands:
        release_probe.run(command, root=root, env=env, operation=operation)


def check_package(project: Path) -> str:
    """Build fresh artifacts and check each wheel in a separate temporary venv."""
    uv = shutil.which("uv")
    if uv is None:
        message = "Install uv before checking the packages"
        raise release_probe.ProbeError(message)
    project = project.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="flexi-package-") as temporary:
        root = Path(temporary).resolve()
        env = release_probe.environment(root)
        uv_command = [uv, "--no-config", "--cache-dir", str(root / "uv-cache")]
        production, staging = root / "dist", root / "test-dist"
        release_probe.run(
            [
                *uv_command,
                "build",
                str(project),
                "--out-dir",
                str(production),
                "--no-create-gitignore",
                "--no-sources",
                "--default-index",
                INDEX,
                "--python",
                sys.executable,
                "--no-python-downloads",
            ],
            root=root,
            env=env,
            operation="Building the production wheel and source archive",
        )
        version = build_staging.build_staging(production, staging)
        pairs = {
            registry: build_staging.distribution_pair(
                directory, registry.package, version
            )
            for registry, directory in (
                (Registry.PYPI, production),
                (Registry.TESTPYPI, staging),
            )
        }
        for registry, (wheel, _) in pairs.items():
            release_probe.check_metadata(wheel, version, registry)
        build_staging.require_same_payload(
            pairs[Registry.PYPI][0], pairs[Registry.TESTPYPI][0]
        )
        release_probe.run(
            [
                *uv_command,
                "tool",
                "run",
                "--isolated",
                "--from",
                "twine==7.0.0",
                "--default-index",
                INDEX,
                "--python",
                sys.executable,
                "--no-python-downloads",
                "twine",
                "check",
                "--strict",
                *(str(path) for pair in pairs.values() for path in pair),
            ],
            root=root,
            env=env,
            operation="Checking both distributions' metadata and README rendering",
        )
        requirements = root / "test-requirements.txt"
        release_probe.run(
            [
                *uv_command,
                "export",
                "--project",
                str(project),
                "--locked",
                "--no-emit-project",
                "--no-hashes",
                "--group",
                "dev",
                "--output-file",
                str(requirements),
            ],
            root=root,
            env=env,
            operation="Exporting the locked test dependencies",
        )
        for registry, (wheel, _) in pairs.items():
            isolated = release_probe.environment(wheel.parent)
            python = release_probe.exercise_wheel(
                wheel, version, registry, uv=uv, env=isolated
            )
            test_installed_package(
                python, wheel, requirements, project, uv=uv, env=isolated
            )
    return version


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        version = check_package(ROOT)
    except (
        release_probe.ProbeError,
        build_staging.BuildError,
        OSError,
        ValueError,
        BadZipFile,
    ) as error:
        print(f"Package check failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Passed: flexi and flexi-test {version}. "
        "Temporary builds and installs removed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
