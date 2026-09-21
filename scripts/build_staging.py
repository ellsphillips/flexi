"""Build flexi-test from a production sdist without changing application files."""

from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

PRODUCTION = "flexi"
STAGING = "flexi-test"
MAX_MEMBER = 20 * 1024 * 1024
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_MEMBERS = 10_000
BUILD_TIMEOUT = 300
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
WHEEL = re.compile(rf"flexi-({VERSION})-py3-none-any\.whl")
PROJECT = re.compile(r"(?ms)^\[project\][ \t]*(?:#[^\n]*)?\r?\n.*?(?=^\[|\Z)")
NAME = re.compile(
    r"(?m)^(?P<prefix>[ \t]*name[ \t]*=[ \t]*)(?P<quote>['\"])"
    r"flexi(?P=quote)(?P<suffix>[ \t]*(?:#[^\r\n]*)?\r?)$"
)
WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)}
)


class BuildError(Exception):
    """A staging build cannot prove that the production application is preserved."""


def member_path(name: str) -> str:
    parts = name.removesuffix("/").split("/")
    if any(
        part in {"", ".", ".."}
        or ":" in part
        or "\\" in part
        or "\x00" in part
        or part.endswith((".", " "))
        or part.split(".")[0].casefold() in WINDOWS_RESERVED
        for part in parts
    ):
        message = "Archive contains an unsafe or non-portable path"
        raise BuildError(message)
    return str(PurePosixPath(*parts))


def require_archive(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
        message = "Distributions must be bounded regular files, not symbolic links"
        raise BuildError(message)


def remember_member(seen: set[str], name: str, size: int, total: int) -> int:
    key = name.casefold()
    if key in seen or len(seen) >= MAX_MEMBERS:
        message = "Archive contains duplicate paths or too many members"
        raise BuildError(message)
    seen.add(key)
    if not 0 <= size <= MAX_MEMBER or total + size > MAX_ARCHIVE:
        message = "Archive expands beyond the allowed size"
        raise BuildError(message)
    return total + size


def source_files(path: Path, root: str) -> dict[str, bytes]:
    require_archive(path)
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    try:
        with gzip.open(path, "rb") as compressed:
            expanded = compressed.read(MAX_ARCHIVE + 1)
        if len(expanded) > MAX_ARCHIVE:
            message = "Source archive expands beyond the allowed size"
            raise BuildError(message)
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for member in archive:
                name = member_path(member.name)
                total = remember_member(seen, name, member.size, total)
                if (name != root and not name.startswith(f"{root}/")) or not (
                    member.isdir() or member.isfile()
                ):
                    message = "Expected regular files under the source project root"
                    raise BuildError(message)
                if member.isdir():
                    continue
                content = archive.extractfile(member)
                if content is None:
                    message = "Source archive member has no content"
                    raise BuildError(message)
                with content:
                    data = content.read(MAX_MEMBER + 1)
                if len(data) != member.size:
                    message = "Source archive member has an invalid size"
                    raise BuildError(message)
                files[name.removeprefix(f"{root}/")] = data
    except (tarfile.TarError, EOFError) as error:
        message = "Cannot read a valid source archive"
        raise BuildError(message) from error
    return files


def wheel_files(path: Path) -> dict[str, bytes]:
    require_archive(path)
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    try:
        with ZipFile(path) as archive:
            for member in archive.infolist():
                name = member_path(member.filename)
                total = remember_member(seen, name, member.file_size, total)
                kind = stat.S_IFMT(member.external_attr >> 16)
                if member.is_dir():
                    if kind not in {0, stat.S_IFDIR}:
                        message = "Wheel contains an invalid directory"
                        raise BuildError(message)
                    continue
                if kind not in {0, stat.S_IFREG}:
                    message = "Wheel contains links or special files"
                    raise BuildError(message)
                with archive.open(member) as content:
                    data = content.read(MAX_MEMBER + 1)
                if len(data) != member.file_size:
                    message = "Wheel member has an invalid size"
                    raise BuildError(message)
                files[name] = data
    except (BadZipFile, RuntimeError) as error:
        message = "Cannot read a valid wheel"
        raise BuildError(message) from error
    return files


def dist_info(files: dict[str, bytes]) -> str:
    directories = {
        name.split("/")[0]
        for name in files
        if name.split("/")[0].endswith(".dist-info")
    }
    if len(directories) != 1:
        message = "Wheel must contain exactly one distribution metadata directory"
        raise BuildError(message)
    return directories.pop()


def require_same_payload(production_wheel: Path, staging_wheel: Path) -> None:
    payloads = []
    for wheel in (production_wheel, staging_wheel):
        files = wheel_files(wheel)
        metadata_directory = dist_info(files)
        payloads.append(
            {
                name: content
                for name, content in files.items()
                if not name.startswith(f"{metadata_directory}/")
            }
        )
    if not payloads[0] or payloads[0] != payloads[1]:
        message = "Staging wheel application payload differs from the production wheel"
        raise BuildError(message)


def require_metadata(content: bytes, package: str, version: str) -> None:
    metadata = BytesParser(policy=policy.default).parsebytes(content)
    if metadata.get_all("Name") != [package] or metadata.get_all("Version") != [
        version
    ]:
        message = "Built distribution metadata has an unexpected name or version"
        raise BuildError(message)


def rename_project(content: bytes, version: str) -> bytes:
    source = content.decode("utf-8")
    before = tomllib.loads(source)
    project = before.get("project")
    if (
        not isinstance(project, dict)
        or project.get("name") != PRODUCTION
        or project.get("version") != version
    ):
        message = "Source pyproject.toml must describe the canonical flexi version"
        raise BuildError(message)
    tables = list(PROJECT.finditer(source))
    if len(tables) != 1 or len(list(NAME.finditer(tables[0].group()))) != 1:
        message = "Source must have one simple quoted project.name assignment"
        raise BuildError(message)
    table = tables[0]
    renamed = NAME.sub(
        lambda match: (
            f"{match['prefix']}{match['quote']}{STAGING}{match['quote']}{match['suffix']}"
        ),
        table.group(),
    )
    result = source[: table.start()] + renamed + source[table.end() :]
    expected = dict(before)
    expected["project"] = {**project, "name": STAGING}
    if tomllib.loads(result) != expected:
        message = "Staging changed project metadata beyond its distribution name"
        raise BuildError(message)
    return result.encode("utf-8")


def distribution_pair(directory: Path, package: str, version: str) -> tuple[Path, Path]:
    normalized = package.replace("-", "_")
    wheel = directory / f"{normalized}-{version}-py3-none-any.whl"
    source = directory / f"{normalized}-{version}.tar.gz"
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or set(directory.iterdir()) != {wheel, source}
    ):
        message = "Expected exactly one canonical wheel and source archive"
        raise BuildError(message)
    require_archive(wheel)
    require_archive(source)
    return wheel, source


def build_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "SYSTEMDRIVE",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def run_build(source: Path, output: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        message = "Install uv to build the staging distributions"
        raise BuildError(message)
    temporary = source.parent / "tmp"
    temporary.mkdir(exist_ok=True)
    environment = build_environment()
    environment.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
    try:
        subprocess.run(  # noqa: S603 - executable resolved on PATH, explicit argument list
            [
                uv,
                "build",
                str(source),
                "--out-dir",
                str(output),
                "--no-sources",
                "--no-config",
                "--no-create-gitignore",
                "--default-index",
                "https://pypi.org/simple/",
                "--cache-dir",
                str(source.parent / "cache"),
                "--python",
                sys.executable,
                "--no-python-downloads",
            ],
            cwd=source,
            env=environment,
            check=True,
            timeout=BUILD_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        message = (
            "Building staging distributions failed; production artifacts are unchanged"
        )
        raise BuildError(message) from error


def build_staging(directory: Path, output: Path) -> str:
    if directory.is_symlink() or not directory.is_dir():
        message = "Production distributions must be a regular directory"
        raise BuildError(message)
    versions = [
        match[1]
        for path in directory.iterdir()
        if (match := WHEEL.fullmatch(path.name))
    ]
    if len(versions) != 1:
        message = "Cannot identify exactly one stable production wheel version"
        raise BuildError(message)
    version = versions[0]
    wheel, source = distribution_pair(directory, PRODUCTION, version)
    original = wheel_files(wheel)
    require_metadata(
        original.get(f"{dist_info(original)}/METADATA", b""), PRODUCTION, version
    )
    files = source_files(source, f"flexi-{version}")
    if "pyproject.toml" not in files:
        message = "Production source archive is missing pyproject.toml"
        raise BuildError(message)
    files["pyproject.toml"] = rename_project(files["pyproject.toml"], version)
    if output.exists() or output.is_symlink():
        message = "Staging output must not already exist; use a fresh output directory"
        raise BuildError(message)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="flexi-staging-", dir=output.parent
    ) as temporary:
        scratch = Path(temporary).resolve()
        checkout = scratch / "source"
        for name, content in files.items():
            target = checkout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        built = scratch / "dist"
        run_build(checkout, built)
        test_wheel, test_source = distribution_pair(built, STAGING, version)
        staged = wheel_files(test_wheel)
        require_metadata(
            staged.get(f"{dist_info(staged)}/METADATA", b""), STAGING, version
        )
        staged_files = source_files(test_source, f"flexi_test-{version}")
        require_metadata(staged_files.get("PKG-INFO", b""), STAGING, version)
        if {
            name: data for name, data in staged_files.items() if name != "PKG-INFO"
        } != {name: data for name, data in files.items() if name != "PKG-INFO"}:
            message = (
                "Staging source differs beyond the project name and generated metadata"
            )
            raise BuildError(message)
        require_same_payload(wheel, test_wheel)
        built.rename(output.resolve())
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--out", type=Path, default=Path("test-dist"))
    args = parser.parse_args()
    try:
        version = build_staging(args.dist, args.out)
    except (BuildError, OSError, ValueError) as error:
        print(f"Staging build stopped: {error}", file=sys.stderr)
        return 1
    print(f"Built {STAGING} {version}; application payload matches {PRODUCTION}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
