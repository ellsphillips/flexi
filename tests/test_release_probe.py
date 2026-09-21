"""A local probe must execute only the verified staged wheel, in temporary paths."""

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
WHEEL = f"flexi-{VERSION}-py3-none-any.whl"
SDIST = f"flexi-{VERSION}.tar.gz"
JSON_URL = f"https://test.pypi.org/pypi/flexi/{VERSION}/json"
WHEEL_URL = f"https://test-files.pythonhosted.org/packages/example/{WHEEL}"


def wheel_bytes(*, version: str = VERSION, dependency: str = "httpx>=0.27") -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            f"flexi-{VERSION}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: flexi\nVersion: {version}\n"
            f"Requires-Dist: {dependency}\n\n",
        )
    return output.getvalue()


@dataclass(frozen=True)
class Invocation:
    command: list[str]
    root: Path
    env: dict[str, str]
    captured: bool
    timeout: int


class Harness:
    def __init__(self, artifacts: Path) -> None:
        self.artifacts = artifacts
        self.body = b""
        self.entries: list[dict[str, object]] = []
        self.url = WHEEL_URL
        self.calls: list[Invocation] = []
        self.requests: list[str] = []
        self.fail_at: int | None = None
        self.timeout_at: int | None = None
        self.set_wheel(wheel_bytes())

    def set_wheel(self, body: bytes) -> None:
        self.body = body
        (self.artifacts / WHEEL).write_bytes(body)
        (self.artifacts / SDIST).write_bytes(b"source archive")
        self.entries = [
            {
                "filename": path.name,
                "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                "url": self.url,
            }
            for path in self.artifacts.iterdir()
        ]

    def metadata(self, url: str, **kwargs: object) -> object:
        self.requests.append(url)
        assert url == JSON_URL
        assert not kwargs.get("token")
        return {"urls": self.entries}

    def opener(self, *handlers: object) -> Harness:
        assert any(isinstance(handler, probe.NoRedirect) for handler in handlers)
        return self

    def open(self, request: Request, *, timeout: float) -> io.BytesIO:
        self.requests.append(request.full_url)
        assert request.full_url == WHEEL_URL
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
    harness = Harness(artifacts)
    monkeypatch.setattr(release_status, "request_json", harness.metadata)
    monkeypatch.setattr(probe, "build_opener", harness.opener)
    monkeypatch.setattr(subprocess, "run", harness.run)
    monkeypatch.setattr(shutil, "which", lambda _name: str(tmp_path / "uv"))
    return harness


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_probe_uses_verified_wheel_and_isolated_paths(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
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

    probe.probe_release(VERSION, harness.artifacts)

    assert len(harness.calls) == 7
    root = harness.calls[0].root
    assert not root.exists()
    assert harness.artifacts.is_dir()
    python = (
        root / "venv" / ("Scripts/python.exe" if platform == "win32" else "bin/python")
    )
    install = harness.calls[1].command
    assert install[install.index("--python") + 1] == str(python)
    assert install[install.index("--index-url") + 1] == "https://pypi.org/simple/"
    assert install[-1] == str(root / WHEEL)
    assert "--no-build" in install
    assert "--no-config" in install
    assert "--extra-index-url" not in install
    assert harness.calls[0].command[-3:] == [
        sys.executable,
        "--no-python-downloads",
        str(root / "venv"),
    ]
    for call in harness.calls:
        assert call.root == root
        assert "must-not-travel" not in call.env.values()
        assert call.env["SystemRoot"] == "C:\\Windows"
        assert Path(call.env["XDG_DATA_HOME"]).is_relative_to(root)
        assert Path(call.env["XDG_CONFIG_HOME"]).is_relative_to(root)
        assert Path(call.env["TEMP"]).is_relative_to(root)
        assert call.captured
        assert call.timeout == probe.COMMAND_TIMEOUT
    assert harness.calls[3].command[:3] == [str(python), "-I", "-c"]
    assert harness.calls[4].command[-1] == "--version"
    assert harness.calls[5].command[-1] == "--help"
    assert harness.calls[6].command == [str(python), "-I", str(probe.SMOKE)]
    assert harness.requests == [JSON_URL, JSON_URL, WHEEL_URL]


def test_download_hash_is_checked_before_any_installation(harness: Harness) -> None:
    harness.body = b"different download"
    with pytest.raises(probe.ProbeError, match=r"Downloaded.*differs"):
        probe.probe_release(VERSION, harness.artifacts)
    assert harness.calls == []


def test_registry_hash_must_match_the_selected_ci_artifact(harness: Harness) -> None:
    harness.entries[0]["digests"] = {"sha256": "0" * 64}
    with pytest.raises(probe.ProbeError, match="differs from the tested"):
        probe.probe_release(VERSION, harness.artifacts)
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
        probe.probe_release(VERSION, harness.artifacts)
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
        probe.probe_release(VERSION, harness.artifacts)
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
        probe.probe_release(VERSION, harness.artifacts)
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
        probe.probe_release(VERSION, harness.artifacts)
    assert str(root) in str(caught.value)
    assert root.exists()


def test_demo_requires_a_terminal_before_any_network_or_install(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(probe.ProbeError, match="interactive terminal"):
        probe.probe_release(VERSION, harness.artifacts, demo=True)
    assert harness.requests == []
    assert harness.calls == []


def test_demo_runs_in_the_same_temporary_environment_until_exit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    probe.probe_release(VERSION, harness.artifacts, demo=True)
    demo = harness.calls[-1]
    assert demo.command[1:] == ["-I", "-m", "flexi", "--demo"]
    assert not demo.captured
    assert demo.timeout == probe.DEMO_TIMEOUT
    assert demo.env == harness.calls[0].env
    assert not demo.root.exists()


def test_download_size_is_bounded(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe, "MAX_WHEEL_BYTES", 2)
    with pytest.raises(probe.ProbeError, match="size limit"):
        probe.probe_release(VERSION, harness.artifacts)
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


@pytest.mark.parametrize(
    "condition",
    ["missing-hash", "matching-hash", "wrong-hash", "version", "origin", "path"],
)
def test_installed_identity_accepts_uv_metadata_and_rejects_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str
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
    monkeypatch.setitem(sys.modules, "flexi", module)
    monkeypatch.setattr(metadata, "distribution", lambda _name: distribution)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "argv", ["-c", VERSION, wheel_uri, digest])
    if condition in {"missing-hash", "matching-hash"}:
        exec(probe.INSTALLED_CHECK, {})  # noqa: S102 - the probe's fixed identity check
    else:
        with pytest.raises(AssertionError):
            exec(probe.INSTALLED_CHECK, {})  # noqa: S102 - fixed check, mocked imports
