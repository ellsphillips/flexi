"""Staging preserves the canonical application while changing distribution identity."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest
from scripts import build_staging as build

VERSION = "1.2.3"
PROJECT = (
    b'# flexi remains the application name\n[project]\nname = "flexi" # distribution\n'
    b'version = "1.2.3"\nauthors = [{name = "flexi"}]\n'
    b'[project.scripts]\nflexi = "flexi.__main__:cli"\n'
)
APPLICATION = {
    "flexi/__init__.py": b"VERSION = 'installed'\n",
    "flexi/app.tcss": b"Screen {}\n",
}
SOURCE = {
    "pyproject.toml": PROJECT,
    **{f"src/{name}": content for name, content in APPLICATION.items()},
    "README.md": b"# flexi\n",
}


def metadata(package: str, version: str = VERSION) -> bytes:
    return f"Metadata-Version: 2.3\nName: {package}\nVersion: {version}\n\n".encode()


def write_source(path: Path, root: str, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def write_wheel(
    path: Path,
    package: str,
    application: dict[str, bytes],
    *,
    version: str = VERSION,
) -> None:
    with ZipFile(path, "w") as archive:
        for name, content in application.items():
            archive.writestr(name, content)
        archive.writestr(
            f"{package.replace('-', '_')}-{VERSION}.dist-info/METADATA",
            metadata(package, version),
        )


class Backend:
    """A build backend that exposes the actual temporary source it received."""

    def __init__(self) -> None:
        self.sources: list[Path] = []
        self.inputs: list[dict[str, bytes]] = []
        self.tamper = ""

    def __call__(self, source: Path, output: Path) -> None:
        self.sources.append(source)
        files = {name: (source / name).read_bytes() for name in SOURCE}
        self.inputs.append(dict(files))
        if self.tamper == "failure":
            message = "backend failed"
            raise build.BuildError(message)
        application = dict(APPLICATION)
        if self.tamper == "wheel-content":
            application["flexi/__init__.py"] = b"changed application"
        elif self.tamper == "wheel-extra":
            application["unreviewed.py"] = b"extra payload"
        elif self.tamper == "wheel-missing":
            del application["flexi/app.tcss"]
        elif self.tamper == "source-content":
            files["src/flexi/__init__.py"] = b"changed source"
        elif self.tamper == "source-extra":
            files["unreviewed.py"] = b"extra source"
        output.mkdir()
        package = "other" if self.tamper == "wheel-name" else build.STAGING
        version = "9.9.9" if self.tamper == "wheel-version" else VERSION
        write_wheel(
            output / f"flexi_test-{VERSION}-py3-none-any.whl",
            package,
            application,
            version=version,
        )
        package = "other" if self.tamper == "source-name" else build.STAGING
        version = "9.9.9" if self.tamper == "source-version" else VERSION
        files["PKG-INFO"] = metadata(package, version)
        write_source(
            output / f"flexi_test-{VERSION}.tar.gz", f"flexi_test-{VERSION}", files
        )


@pytest.fixture
def production(tmp_path: Path) -> Path:
    directory = tmp_path / "production"
    directory.mkdir()
    write_wheel(
        directory / f"flexi-{VERSION}-py3-none-any.whl", build.PRODUCTION, APPLICATION
    )
    write_source(
        directory / f"flexi-{VERSION}.tar.gz",
        f"flexi-{VERSION}",
        {**SOURCE, "PKG-INFO": metadata(build.PRODUCTION)},
    )
    return directory


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Backend:
    backend = Backend()
    monkeypatch.setattr(build, "run_build", backend)
    return backend


def test_build_uses_only_canonical_source_and_preserves_production_bytes(
    production: Path, backend: Backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {path.name: path.read_bytes() for path in production.iterdir()}
    unrelated = tmp_path / "unrelated-checkout"
    unrelated.mkdir()
    (unrelated / "pyproject.toml").write_text("must not be read", encoding="utf-8")
    monkeypatch.chdir(unrelated)
    output = tmp_path / "test-dist"
    assert build.build_staging(production, output) == VERSION
    assert {path.name for path in output.iterdir()} == {
        f"flexi_test-{VERSION}.tar.gz",
        f"flexi_test-{VERSION}-py3-none-any.whl",
    }
    assert {path.name: path.read_bytes() for path in production.iterdir()} == before
    assert len(backend.inputs) == 1
    assert backend.inputs[0] == {
        **SOURCE,
        "pyproject.toml": PROJECT.replace(b'name = "flexi"', b'name = "flexi-test"', 1),
    }
    assert not backend.sources[0].parent.exists()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("quote", [b'"', b"'"])
def test_only_project_name_changes_with_comments_quotes_and_line_endings(
    newline: bytes, quote: bytes
) -> None:
    source = PROJECT.replace(b'name = "flexi"', b"name = " + quote + b"flexi" + quote)
    source = source.replace(b"\n", newline)
    result = build.rename_project(source, VERSION)
    assert result == source.replace(b"flexi" + quote, b"flexi-test" + quote, 1)
    before = tomllib.loads(source.decode())
    after = tomllib.loads(result.decode())
    before["project"]["name"] = build.STAGING
    assert before == after


@pytest.mark.parametrize(
    "source",
    [
        PROJECT.replace(b'name = "flexi"', b'name = "other"'),
        PROJECT.replace(b'"1.2.3"', b'"9.9.9"'),
        b"[project]\nversion = '1.2.3'\n",
        PROJECT.replace(b'name = "flexi"', b'name = "flexi"\nname = "flexi"'),
    ],
)
def test_malformed_or_mismatched_project_metadata_is_rejected(source: bytes) -> None:
    with pytest.raises((build.BuildError, ValueError)):
        build.rename_project(source, VERSION)


@pytest.mark.parametrize(
    "tamper",
    [
        "failure",
        "wheel-content",
        "wheel-extra",
        "wheel-missing",
        "wheel-name",
        "wheel-version",
        "source-content",
        "source-extra",
        "source-name",
        "source-version",
    ],
)
def test_failed_or_changed_build_has_no_published_output(
    production: Path, backend: Backend, tmp_path: Path, tamper: str
) -> None:
    backend.tamper = tamper
    output = tmp_path / "test-dist"
    with pytest.raises(build.BuildError):
        build.build_staging(production, output)
    assert not output.exists()
    assert backend.sources
    assert not backend.sources[0].parent.exists()
    assert not list(tmp_path.glob("flexi-staging-*"))


@pytest.mark.parametrize("extra", ["README.md", "flexi-2.0.0-py3-none-any.whl"])
def test_ambiguous_production_artifacts_never_reach_the_backend(
    production: Path, backend: Backend, tmp_path: Path, extra: str
) -> None:
    (production / extra).write_bytes(b"unexpected")
    with pytest.raises(build.BuildError):
        build.build_staging(production, tmp_path / "test-dist")
    assert backend.sources == []


def test_existing_output_is_preserved(
    production: Path, backend: Backend, tmp_path: Path
) -> None:
    output = tmp_path / "test-dist"
    output.mkdir()
    (output / "keep").write_bytes(b"existing output")
    with pytest.raises(build.BuildError, match="already exist"):
        build.build_staging(production, output)
    assert (output / "keep").read_bytes() == b"existing output"
    assert backend.sources == []


@pytest.mark.parametrize("suffix", ["-py3-none-any.whl", ".tar.gz"])
def test_linked_production_artifacts_are_rejected(
    production: Path, backend: Backend, tmp_path: Path, suffix: str
) -> None:
    original = production / f"flexi-{VERSION}{suffix}"
    outside = tmp_path / "outside"
    original.rename(outside)
    try:
        original.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    with pytest.raises(build.BuildError, match="symbolic links"):
        build.build_staging(production, tmp_path / "test-dist")
    assert backend.sources == []


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "folder/../../escape",
        "folder\\escape",
        "C:/escape",
        "CON",
        "name.",
        "folder/./file",
    ],
)
def test_tar_paths_cannot_escape_or_alias_on_any_supported_os(
    tmp_path: Path, name: str
) -> None:
    archive = tmp_path / "source.tar.gz"
    write_source(archive, "flexi-1.2.3", {name: b"data"})
    with pytest.raises(build.BuildError, match="path"):
        build.source_files(archive, "flexi-1.2.3")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_tar_links_and_special_files_are_rejected(tmp_path: Path, kind: bytes) -> None:
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("flexi-1.2.3/linked")
        member.type = kind
        member.linkname = "../../outside"
        archive.addfile(member)
    with pytest.raises(build.BuildError, match="regular files"):
        build.source_files(path, "flexi-1.2.3")


def test_tar_expansion_is_bounded_before_parsing_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "source.tar.gz"
    write_source(path, "flexi-1.2.3", {"large": bytes(10_000)})
    monkeypatch.setattr(build, "MAX_ARCHIVE", 512)
    with pytest.raises(build.BuildError, match="expands"):
        build.source_files(path, "flexi-1.2.3")


def test_case_colliding_archive_paths_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source.tar.gz"
    write_source(path, "flexi-1.2.3", {"file": b"one", "FILE": b"two"})
    with pytest.raises(build.BuildError, match="duplicate paths"):
        build.source_files(path, "flexi-1.2.3")


def test_wheel_comparison_allows_only_distribution_metadata_differences(
    production: Path, tmp_path: Path
) -> None:
    staging = tmp_path / "staging.whl"
    write_wheel(staging, build.STAGING, APPLICATION)
    build.require_same_payload(
        production / f"flexi-{VERSION}-py3-none-any.whl", staging
    )


def test_wheel_comparison_refuses_symlink_payloads(
    production: Path, tmp_path: Path
) -> None:
    staging = tmp_path / "staging.whl"
    write_wheel(staging, build.STAGING, APPLICATION)
    with ZipFile(staging, "a") as archive:
        link = ZipInfo("shortcut")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "../outside")
    with pytest.raises(build.BuildError, match="links"):
        build.require_same_payload(
            production / f"flexi-{VERSION}-py3-none-any.whl", staging
        )


def test_build_command_has_explicit_tools_indexes_and_no_inherited_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(
        command: list[str], *, cwd: Path, env: dict[str, str], check: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path
        assert check
        assert timeout == build.BUILD_TIMEOUT
        assert "secret" not in env.values()
        assert env["TMPDIR"] == env["TMP"] == env["TEMP"]
        assert Path(env["TMPDIR"]).is_relative_to(cwd.parent)
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    for name in ("GH_TOKEN", "GITHUB_TOKEN", "UV_INDEX", "PYTHONPATH", "PIP_INDEX_URL"):
        monkeypatch.setenv(name, "secret")
    monkeypatch.setattr(shutil, "which", lambda _name: "/trusted/uv")
    monkeypatch.setattr(subprocess, "run", run)
    build.run_build(tmp_path, tmp_path / "built")
    assert len(calls) == 1
    command = calls[0]
    assert command[:3] == ["/trusted/uv", "build", str(tmp_path)]
    assert "--no-sources" in command
    assert "--no-config" in command
    assert command[command.index("--default-index") + 1] == "https://pypi.org/simple/"
    assert command[command.index("--python") + 1] == sys.executable


def test_cli_uses_the_requested_directories(
    production: Path, backend: Backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "test-dist"
    monkeypatch.setattr(
        sys, "argv", ["build_staging", "--dist", str(production), "--out", str(output)]
    )
    assert build.main() == 0
    assert output.is_dir()
