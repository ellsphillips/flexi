"""A local probe verifies distinct staging and production wheels in isolated paths."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from threading import Event
from types import ModuleType
from urllib.request import Request
from zipfile import ZipFile

import pytest
from scripts import release_probe as probe
from scripts import release_status

VERSION = "1.2.3"
PRODUCTION_WHEEL = f"flexi-{VERSION}-py3-none-any.whl"
WHEEL = f"flexi_test-{VERSION}-py3-none-any.whl"
SDIST = f"flexi_test-{VERSION}.tar.gz"
JSON_URL = f"https://test.pypi.org/pypi/flexi-test/{VERSION}/json"
WHEEL_URL = f"https://test-files.pythonhosted.org/packages/example/{WHEEL}"


def wheel_bytes(
    *,
    version: str = VERSION,
    identity_version: str = VERSION,
    dependency: str = "httpx>=0.27",
    package: str = "flexi-test",
    payload: bytes = b"application code",
    extra_metadata: str = "",
) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            f"{package.replace('-', '_')}-{identity_version}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: {package}\nVersion: {version}\n"
            f"Requires-Dist: {dependency}\n{extra_metadata}\n",
        )
        archive.writestr("flexi/__init__.py", payload)
    return output.getvalue()


@dataclass(frozen=True)
class Invocation:
    command: list[str]
    root: Path
    env: dict[str, str]
    captured: bool
    timeout: int


class Harness:
    def __init__(self, artifacts: Path, production: Path) -> None:
        self.artifacts = artifacts
        self.production = production
        (production / PRODUCTION_WHEEL).write_bytes(wheel_bytes(package="flexi"))
        (production / f"flexi-{VERSION}.tar.gz").write_bytes(b"production source")
        self.body = b""
        self.staging_version = VERSION
        self.entries: list[dict[str, object]] = []
        self.url = WHEEL_URL
        self.calls: list[Invocation] = []
        self.requests: list[str] = []
        self.fail_at: int | None = None
        self.timeout_at: int | None = None
        self.set_wheel(wheel_bytes())

    def set_wheel(self, body: bytes) -> None:
        self.body = body
        for filename in release_status.filenames(
            self.staging_version, release_status.Registry.TESTPYPI
        ):
            (self.artifacts / filename).write_bytes(
                body if filename.endswith(".whl") else b"source archive"
            )
        self.entries = [
            {
                "filename": path.name,
                "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                "url": self.url,
            }
            for path in self.artifacts.iterdir()
        ]

    def use_preview(self) -> None:
        self.staging_version = f"{VERSION}.dev42"
        for path in self.artifacts.iterdir():
            path.unlink()
        filename = probe.wheel_filename(
            self.staging_version, release_status.Registry.TESTPYPI
        )
        self.url = f"https://test-files.pythonhosted.org/packages/example/{filename}"
        self.set_wheel(
            wheel_bytes(
                version=self.staging_version, identity_version=self.staging_version
            )
        )

    def metadata(self, url: str, **kwargs: object) -> object:
        self.requests.append(url)
        assert url == (
            f"https://test.pypi.org/pypi/flexi-test/{self.staging_version}/json"
        )
        assert not kwargs.get("token")
        return {"urls": self.entries}

    def opener(self, *handlers: object) -> Harness:
        assert any(isinstance(handler, probe.NoRedirect) for handler in handlers)
        return self

    def open(self, request: Request, *, timeout: float) -> io.BytesIO:
        self.requests.append(request.full_url)
        assert request.full_url == self.url
        assert timeout == probe.SOCKET_TIMEOUT
        assert request.get_header("Authorization") is None
        return io.BytesIO(self.body)

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
        assert check
        assert encoding == "utf-8"
        assert errors == "replace"
        self.calls.append(Invocation(command, cwd, dict(env), capture_output, timeout))
        if len(self.calls) == self.fail_at:
            raise subprocess.CalledProcessError(
                2, command, stderr="dependency unavailable"
            )
        if len(self.calls) == self.timeout_at:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    production = tmp_path / "production"
    production.mkdir()
    harness = Harness(artifacts, production)
    monkeypatch.setattr(release_status, "request_json", harness.metadata)
    monkeypatch.setattr(probe, "build_opener", harness.opener)
    monkeypatch.setattr(subprocess, "run", harness.run)
    monkeypatch.setattr(shutil, "which", lambda _name: str(tmp_path / "uv"))
    return harness


@pytest.mark.parametrize("platform", ["darwin", "win32"])
@pytest.mark.parametrize("preview", [False, True])
def test_probe_uses_verified_wheel_and_isolated_paths(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, platform: str, preview: bool
) -> None:
    if preview:
        harness.use_preview()
    monkeypatch.setattr(sys, "platform", platform)
    for key in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "UV_INDEX",
        "UV_SYSTEM_PYTHON",
        "PIP_EXTRA_INDEX_URL",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.setenv(key, "must-not-travel")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")

    probe.probe_release(
        VERSION,
        harness.production,
        harness.artifacts,
        staging_version=harness.staging_version,
    )

    assert len(harness.calls) == 14
    staging_root = harness.calls[0].root
    production_root = harness.calls[7].root
    assert staging_root != production_root
    assert staging_root.parent == production_root.parent
    assert not staging_root.parent.exists()
    assert harness.artifacts.is_dir()
    assert harness.production.is_dir()
    staged_wheel = probe.wheel_filename(
        harness.staging_version, release_status.Registry.TESTPYPI
    )
    assert (harness.artifacts / staged_wheel).read_bytes() != (
        harness.production / PRODUCTION_WHEEL
    ).read_bytes()
    for offset, root, filename, package, version in (
        (0, staging_root, staged_wheel, "flexi-test", harness.staging_version),
        (7, production_root, PRODUCTION_WHEEL, "flexi", VERSION),
    ):
        python = (
            root
            / "venv"
            / ("Scripts/python.exe" if platform == "win32" else "bin/python")
        )
        calls = harness.calls[offset : offset + 7]
        install = calls[1].command
        assert install[install.index("--python") + 1] == str(python)
        assert install[install.index("--index-url") + 1] == "https://pypi.org/simple/"
        assert install[-1] == str(root / filename)
        assert "--no-build" in install
        assert "--no-config" in install
        assert "--extra-index-url" not in install
        assert calls[0].command[-3:] == [
            sys.executable,
            "--no-python-downloads",
            str(root / "venv"),
        ]
        for call in calls:
            assert call.root == root
            assert "must-not-travel" not in call.env.values()
            environment = {key.upper(): value for key, value in call.env.items()}
            assert environment["SYSTEMROOT"] == "C:\\Windows"
            assert Path(call.env["XDG_DATA_HOME"]).is_relative_to(root)
            assert Path(call.env["XDG_CONFIG_HOME"]).is_relative_to(root)
            assert Path(call.env["TEMP"]).is_relative_to(root)
            assert call.captured
            assert call.timeout == probe.COMMAND_TIMEOUT
        assert calls[3].command[:3] == [str(python), "-I", "-c"]
        assert calls[3].command[-4:-2] == [package, version]
        assert calls[4].command[-1] == "--version"
        assert calls[5].command[-1] == "--help"
        assert calls[6].command == [str(python), "-I", str(probe.SMOKE)]
    metadata_url = (
        f"https://test.pypi.org/pypi/flexi-test/{harness.staging_version}/json"
    )
    assert harness.requests == [metadata_url, metadata_url, harness.url]


def test_download_hash_is_checked_before_any_installation(harness: Harness) -> None:
    harness.body = b"different download"
    with pytest.raises(probe.ProbeError, match=r"Downloaded.*differs"):
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert harness.calls == []


def test_registry_hash_must_match_the_selected_ci_artifact(harness: Harness) -> None:
    harness.entries[0]["digests"] = {"sha256": "0" * 64}
    with pytest.raises(probe.ProbeError, match="differs from the tested"):
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert harness.requests == [JSON_URL]
    assert harness.calls == []


@pytest.mark.parametrize(
    "url",
    [
        f"http://test-files.pythonhosted.org/{WHEEL}",
        f"https://files.pythonhosted.org/{WHEEL}",
        f"https://test-files.pythonhosted.org.evil.example/{WHEEL}",
        f"https://user:password@test-files.pythonhosted.org/{WHEEL}",
        f"https://test-files.pythonhosted.org:8443/{WHEEL}",
        f"https://test-files.pythonhosted.org/{WHEEL}?token=secret",
        "https://test-files.pythonhosted.org/different.whl",
    ],
)
def test_untrusted_wheel_urls_are_rejected(harness: Harness, url: str) -> None:
    for entry in harness.entries:
        entry["url"] = url
    with pytest.raises(probe.ProbeError, match="unexpected download URL"):
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert WHEEL_URL not in harness.requests
    assert harness.calls == []


def test_download_redirects_are_not_followed() -> None:
    with pytest.raises(probe.ProbeError, match="redirected"):
        probe.NoRedirect().redirect_request(newurl="https://evil.example/wheel")


@pytest.mark.parametrize(
    ("version", "dependency", "message"),
    [
        ("9.9.9", "httpx>=0.27", "requested Flexi release"),
        (VERSION, "httpx @ https://evil.example/dependency.whl", "direct-URL"),
    ],
)
def test_wheel_metadata_must_match_before_installing(
    harness: Harness, version: str, dependency: str, message: str
) -> None:
    harness.set_wheel(wheel_bytes(version=version, dependency=dependency))
    with pytest.raises(probe.ProbeError, match=message):
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert harness.calls == []


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_failed_commands_clean_up_and_report_the_operation(
    harness: Harness, failure: str
) -> None:
    if failure == "exit":
        harness.fail_at = 2
    else:
        harness.timeout_at = 2
    with pytest.raises(probe.ProbeError, match="Installing") as caught:
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    if failure == "exit":
        assert "dependency unavailable" in str(caught.value)
    assert len(harness.calls) == 2
    assert not harness.calls[0].root.exists()


def test_cleanup_failure_reports_the_temporary_directory(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "locked-probe"
    root.mkdir()

    @contextmanager
    def locked_directory(*, prefix: str) -> Iterator[str]:
        yield str(root)
        message = "files remain open"
        raise PermissionError(message)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", locked_directory)
    with pytest.raises(probe.ProbeError, match="close any process") as caught:
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert str(root) in str(caught.value)
    assert root.exists()


def test_demo_requires_a_terminal_before_any_network_or_install(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(probe.ProbeError, match="interactive terminal"):
        probe.probe_release(VERSION, harness.production, harness.artifacts, demo=True)
    assert harness.requests == []
    assert harness.calls == []


@pytest.mark.parametrize("preview", [False, True])
def test_demo_uses_production_after_both_temporary_installs_pass(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, preview: bool
) -> None:
    if preview:
        harness.use_preview()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    probe.probe_release(
        VERSION,
        harness.production,
        harness.artifacts,
        staging_version=harness.staging_version,
        demo=True,
    )
    demo = harness.calls[-1]
    assert demo.command[1:] == ["-I", "-m", "flexi", "--demo"]
    assert not demo.captured
    assert demo.timeout == probe.DEMO_TIMEOUT
    assert len(harness.calls) == 15
    assert demo.env == harness.calls[7].env
    assert demo.env != harness.calls[0].env
    assert demo.root == harness.calls[7].root
    assert not demo.root.exists()


def test_download_size_is_bounded(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe, "MAX_WHEEL_BYTES", 2)
    with pytest.raises(probe.ProbeError, match="size limit"):
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert harness.calls == []


def test_download_deadline_covers_dns_without_late_file_writes(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    released = Event()
    completed = Event()

    def stalled(*_args: object, **_kwargs: object) -> io.BytesIO:
        released.wait(timeout=5)
        completed.set()
        return io.BytesIO(harness.body)

    monkeypatch.setattr(harness, "open", stalled)
    monkeypatch.setattr(probe, "DOWNLOAD_TIMEOUT", 0.01)
    destination = tmp_path / WHEEL
    try:
        with pytest.raises(probe.ProbeError, match="timed out"):
            probe.download_wheel(
                WHEEL_URL, hashlib.sha256(harness.body).hexdigest(), destination
            )
    finally:
        released.set()
        assert completed.wait(timeout=5)
    assert not destination.exists()


@dataclass(frozen=True)
class InstalledDistribution:
    version: str
    origin: dict[str, object]

    def read_text(self, _filename: str) -> str:
        return json.dumps(self.origin)


@pytest.mark.parametrize("package", ["flexi", "flexi-test"])
@pytest.mark.parametrize(
    "condition",
    [
        "missing-hash",
        "matching-hash",
        "wrong-hash",
        "version",
        "app-version",
        "origin",
        "path",
    ],
)
def test_installed_identity_accepts_uv_metadata_and_rejects_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str, package: str
) -> None:
    venv = tmp_path / "venv"
    wheel_uri = (tmp_path / WHEEL).as_uri()
    digest = "a" * 64
    origin: dict[str, object] = {"url": wheel_uri, "archive_info": {}}
    if condition in {"matching-hash", "wrong-hash"}:
        origin["archive_info"] = {
            "hashes": {"sha256": digest if condition == "matching-hash" else "b" * 64}
        }
    if condition == "origin":
        origin["url"] = "https://test.pypi.org/unrelated.whl"
    distribution = InstalledDistribution(
        "9.9.9" if condition == "version" else VERSION, origin
    )
    module = ModuleType("flexi")
    module.__file__ = str(
        (tmp_path if condition == "path" else venv) / "flexi" / "__init__.py"
    )
    monkeypatch.setattr(
        module,
        "version",
        lambda: "unknown" if condition == "app-version" else VERSION,
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "flexi", module)

    def installed(name: str) -> InstalledDistribution:
        assert name == package
        return distribution

    monkeypatch.setattr(metadata, "distribution", installed)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "argv", ["-c", package, VERSION, wheel_uri, digest])
    if condition in {"missing-hash", "matching-hash"}:
        exec(probe.INSTALLED_CHECK, {})  # noqa: S102 - the probe's fixed identity check
    else:
        with pytest.raises(AssertionError):
            exec(probe.INSTALLED_CHECK, {})  # noqa: S102 - fixed check, mocked imports


def test_staging_application_payload_must_equal_production(harness: Harness) -> None:
    harness.set_wheel(wheel_bytes(payload=b"different application"))
    with pytest.raises(probe.ProbeError, match="payload"):
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert harness.calls == []


def test_registry_artifact_directories_cannot_be_swapped(harness: Harness) -> None:
    with pytest.raises(probe.ProbeError, match="exactly"):
        probe.probe_release(VERSION, harness.artifacts, harness.production)
    assert harness.requests == []
    assert harness.calls == []


@pytest.mark.parametrize("condition", ["package", "version", "direct-url"])
def test_production_metadata_is_checked_before_either_installation(
    harness: Harness, condition: str
) -> None:
    body = wheel_bytes(
        package="flexi-test" if condition == "package" else "flexi",
        version="9.9.9" if condition == "version" else VERSION,
        dependency="httpx @ https://evil.example/wheel"
        if condition == "direct-url"
        else "httpx>=0.27",
    )
    (harness.production / PRODUCTION_WHEEL).write_bytes(body)
    with pytest.raises(probe.ProbeError):
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert harness.calls == []


def test_production_failure_prevents_demo_and_cleans_both_environments(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    harness.fail_at = 9
    with pytest.raises(probe.ProbeError, match=r"Installing.*flexi"):
        probe.probe_release(VERSION, harness.production, harness.artifacts, demo=True)
    assert len(harness.calls) == 9
    assert all("--demo" not in call.command for call in harness.calls)
    assert not harness.calls[0].root.parent.exists()


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("header", ["Name: unexpected\n", "Version: 9.9.9\n"])
def test_duplicate_metadata_identity_is_rejected_before_installation(
    harness: Harness, production: bool, header: str
) -> None:
    body = wheel_bytes(
        package="flexi" if production else "flexi-test", extra_metadata=header
    )
    if production:
        (harness.production / PRODUCTION_WHEEL).write_bytes(body)
    else:
        harness.set_wheel(body)
    with pytest.raises(probe.ProbeError, match="requested Flexi release"):
        probe.probe_release(VERSION, harness.production, harness.artifacts)
    assert harness.calls == []


@pytest.mark.parametrize("staging_version", ["1.2.4.dev42", "1.2.4", "1.2.3.dev042"])
def test_staging_identity_must_be_canonical_and_share_the_production_base(
    harness: Harness, staging_version: str
) -> None:
    with pytest.raises(probe.ProbeError):
        probe.probe_release(
            VERSION,
            harness.production,
            harness.artifacts,
            staging_version=staging_version,
        )
    assert harness.requests == []
    assert harness.calls == []


def test_missing_preview_registry_files_never_fall_back_to_stable(
    harness: Harness,
) -> None:
    harness.use_preview()
    harness.entries = []
    with pytest.raises(probe.ProbeError, match="both distributions"):
        probe.probe_release(
            VERSION,
            harness.production,
            harness.artifacts,
            staging_version=harness.staging_version,
        )
    assert harness.requests == [
        f"https://test.pypi.org/pypi/flexi-test/{harness.staging_version}/json"
    ]
    assert harness.calls == []


def test_preview_metadata_must_identify_the_expected_preview(harness: Harness) -> None:
    harness.use_preview()
    harness.set_wheel(
        wheel_bytes(version=VERSION, identity_version=harness.staging_version)
    )
    with pytest.raises(probe.ProbeError, match="requested Flexi release"):
        probe.probe_release(
            VERSION,
            harness.production,
            harness.artifacts,
            staging_version=harness.staging_version,
        )
    assert harness.calls == []


def test_failed_preview_install_cleans_up_without_starting_production(
    harness: Harness,
) -> None:
    harness.use_preview()
    harness.fail_at = 2
    with pytest.raises(probe.ProbeError, match=r"Installing.*flexi-test"):
        probe.probe_release(
            VERSION,
            harness.production,
            harness.artifacts,
            staging_version=harness.staging_version,
        )
    assert len(harness.calls) == 2
    assert not harness.calls[0].root.parent.exists()
