"""Release recovery must preserve published bytes, tags, and existing drafts."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
from email.message import Message
from pathlib import Path
from threading import Event
from types import ModuleType
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_status.py"
VERSION = "1.2.3"
SHA = "a" * 40
OTHER_SHA = "b" * 40
REPOSITORY = "example/flexi"
GITHUB = f"https://api.github.com/repos/{REPOSITORY}"
PYPI = f"https://pypi.org/pypi/flexi/{VERSION}/json"
TEST_CREDENTIAL = "invalid-test-credential"


@pytest.fixture
def release_script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("flexi_release_status", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def refuse(*_args: object, **_kwargs: object) -> None:
        message = "release tests cannot make network requests"
        raise OSError(message)

    monkeypatch.setattr(module, "urlopen", refuse)
    return module


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    directory = tmp_path / "dist"
    directory.mkdir()
    (directory / f"flexi-{VERSION}-py3-none-any.whl").write_bytes(b"tested wheel")
    (directory / f"flexi-{VERSION}.tar.gz").write_bytes(b"tested source archive")
    return directory


class Remote:
    def __init__(self, script: ModuleType, artifacts: Path) -> None:
        self.script = script
        self.files = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts.iterdir()
        }
        self.tag: str | None = None
        self.release = False
        self.writes: list[str] = []
        self.race: str | None = None

    def request(
        self,
        url: str,
        *,
        token: str = "",
        payload: dict[str, object] | None = None,
        missing_ok: bool = False,
    ) -> object:
        if url == PYPI:
            assert token == "", "GitHub credentials must never reach PyPI"
            return {
                "urls": [
                    {"filename": name, "digests": {"sha256": digest}}
                    for name, digest in self.files.items()
                ]
            }
        assert url.startswith(f"{GITHUB}/")
        assert token == TEST_CREDENTIAL
        if payload is None:
            if url == f"{GITHUB}/git/ref/tags/v{VERSION}":
                assert missing_ok
                return (
                    None
                    if self.tag is None
                    else {"object": {"type": "commit", "sha": self.tag}}
                )
            assert url == f"{GITHUB}/releases?per_page=100&page=1"
            return [{"tag_name": f"v{VERSION}", "draft": True}] if self.release else []

        self.writes.append(url)
        if url == f"{GITHUB}/git/refs":
            assert payload == {"ref": f"refs/tags/v{VERSION}", "sha": SHA}
            self.tag = SHA
            if self.race in {"tag", "conflicting-tag"}:
                if self.race == "conflicting-tag":
                    self.tag = OTHER_SHA
                raise self.script.RemoteError(422)
        else:
            assert url == f"{GITHUB}/releases"
            assert payload == {
                "tag_name": f"v{VERSION}",
                "target_commitish": SHA,
                "name": f"v{VERSION}",
                "draft": True,
                "generate_release_notes": True,
            }
            self.release = True
            if self.race == "release":
                raise self.script.RemoteError(422)
        return {}


@pytest.fixture
def remote(
    release_script: ModuleType, artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> Remote:
    remote = Remote(release_script, artifacts)
    monkeypatch.setattr(release_script, "request_json", remote.request)
    return remote


@pytest.fixture
def github(release_script: ModuleType) -> Any:
    return release_script.GitHub(REPOSITORY, TEST_CREDENTIAL)


@pytest.mark.parametrize(
    "version",
    ["1.2", "01.2.3", "1.2.3rc1", "1.2.3+build", "1.2.3\n", "1.2.3٤", None],
)
def test_non_stable_versions_are_refused(
    release_script: ModuleType, version: object
) -> None:
    with pytest.raises(release_script.ReleaseError, match=r"stable X\.Y\.Z"):
        release_script.validate_version(version)


@pytest.mark.parametrize("version", ["0.0.0", "0.3.0", "10.22.333"])
def test_stable_versions_are_accepted(release_script: ModuleType, version: str) -> None:
    assert release_script.validate_version(version) == version


@pytest.mark.parametrize("files", [0, 1, 2])
@pytest.mark.parametrize("tagged", [False, True])
@pytest.mark.parametrize("released", [False, True])
def test_guard_only_skips_a_complete_release(
    release_script: ModuleType,
    remote: Remote,
    github: Any,
    files: int,
    tagged: bool,
    released: bool,
) -> None:
    remote.files = dict(list(remote.files.items())[:files])
    remote.tag = SHA if tagged else None
    remote.release = released

    assert release_script.needs_publication(VERSION, SHA, github) is not (
        files == 2 and tagged and released
    )
    assert remote.writes == []


def test_completed_version_on_a_later_documentation_commit_is_a_noop(
    release_script: ModuleType, remote: Remote, github: Any
) -> None:
    remote.tag = OTHER_SHA
    remote.release = True

    assert release_script.needs_publication(VERSION, SHA, github) is False
    assert remote.writes == []


@pytest.mark.parametrize("partial_upload", [False, True])
def test_incomplete_release_cannot_be_resumed_from_a_different_tagged_commit(
    release_script: ModuleType, remote: Remote, github: Any, partial_upload: bool
) -> None:
    remote.tag = OTHER_SHA
    remote.release = False
    if partial_upload:
        remote.files.pop(next(iter(remote.files)))

    with pytest.raises(release_script.ReleaseError, match="Retry its original run"):
        release_script.needs_publication(VERSION, SHA, github)
    assert remote.writes == []


def test_matching_partial_upload_is_safe_to_resume(
    release_script: ModuleType, remote: Remote, artifacts: Path
) -> None:
    remote.files.pop(next(iter(remote.files)))
    release_script.verify_artifacts(artifacts, VERSION)


def test_published_bytes_must_match_before_any_tag_or_release_write(
    release_script: ModuleType, remote: Remote, github: Any, artifacts: Path
) -> None:
    remote.files[next(iter(remote.files))] = "0" * 64
    with pytest.raises(release_script.ReleaseError, match="differs from the tested"):
        release_script.finalize(artifacts, VERSION, SHA, github)
    assert remote.writes == []


def test_finalization_waits_for_both_distributions(
    release_script: ModuleType, remote: Remote, github: Any, artifacts: Path
) -> None:
    remote.files.pop(next(iter(remote.files)))
    with pytest.raises(release_script.ReleaseError, match="both distributions"):
        release_script.finalize(artifacts, VERSION, SHA, github)
    assert remote.writes == []


@pytest.mark.parametrize("initial", ["neither", "tag", "tag-and-draft"])
def test_finalization_is_idempotent_and_preserves_existing_objects(
    release_script: ModuleType,
    remote: Remote,
    github: Any,
    artifacts: Path,
    initial: str,
) -> None:
    remote.tag = None if initial == "neither" else SHA
    remote.release = initial == "tag-and-draft"
    expected_writes = {"neither": 2, "tag": 1, "tag-and-draft": 0}[initial]

    release_script.finalize(artifacts, VERSION, SHA, github)
    release_script.finalize(artifacts, VERSION, SHA, github)

    assert remote.tag == SHA
    assert remote.release
    assert len(remote.writes) == expected_writes


@pytest.mark.parametrize("race", ["tag", "release"])
def test_matching_object_created_concurrently_is_accepted(
    release_script: ModuleType,
    remote: Remote,
    github: Any,
    artifacts: Path,
    race: str,
) -> None:
    remote.race = race
    release_script.finalize(artifacts, VERSION, SHA, github)
    assert remote.tag == SHA
    assert remote.release


def test_conflicting_tag_created_concurrently_is_never_replaced(
    release_script: ModuleType, remote: Remote, github: Any, artifacts: Path
) -> None:
    remote.race = "conflicting-tag"
    with pytest.raises(release_script.ReleaseError, match="belongs to"):
        release_script.finalize(artifacts, VERSION, SHA, github)
    assert remote.tag == OTHER_SHA
    assert not remote.release
    assert remote.writes == [f"{GITHUB}/git/refs"]


def test_missing_or_extra_local_artifacts_are_refused(
    release_script: ModuleType, artifacts: Path
) -> None:
    (artifacts / "unexpected.whl").touch()
    with pytest.raises(release_script.ReleaseError, match="exactly"):
        release_script.verify_artifacts(artifacts, VERSION)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"urls": [None]},
        {"urls": [{"filename": []}]},
        {"urls": [{"filename": "different-project.whl"}]},
        {"urls": [{"filename": f"flexi-{VERSION}.tar.gz", "digests": {}}]},
    ],
)
def test_unverifiable_pypi_metadata_is_refused(
    release_script: ModuleType, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    monkeypatch.setattr(release_script, "request_json", lambda *_a, **_kw: payload)
    with pytest.raises(release_script.ReleaseError):
        release_script.published_files(VERSION)


def test_annotated_tags_are_resolved_to_their_commit(
    release_script: ModuleType, monkeypatch: pytest.MonkeyPatch, github: Any
) -> None:
    requested: list[str] = []

    def request(url: str, **_kwargs: object) -> object:
        requested.append(url)
        if url.endswith(f"git/ref/tags/v{VERSION}"):
            return {"object": {"type": "tag", "sha": OTHER_SHA}}
        assert url.endswith(f"git/tags/{OTHER_SHA}")
        return {"object": {"type": "commit", "sha": SHA}}

    monkeypatch.setattr(release_script, "request_json", request)
    assert github.tag_sha(VERSION) == SHA
    assert len(requested) == 2


def test_draft_release_on_a_later_page_is_found(
    release_script: ModuleType, monkeypatch: pytest.MonkeyPatch, github: Any
) -> None:
    def request(url: str, **_kwargs: object) -> object:
        if url.endswith("page=1"):
            return [{"tag_name": "another-version"}] * 100
        assert url.endswith("page=2")
        return [{"tag_name": f"v{VERSION}", "draft": True}]

    monkeypatch.setattr(release_script, "request_json", request)
    assert github.has_release(VERSION)


def test_http_requests_have_time_and_size_bounds(
    release_script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[Request] = []

    def answer(request: Request, *, timeout: float) -> io.BytesIO:
        received.append(request)
        assert timeout == release_script.REQUEST_TIMEOUT
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr(release_script, "urlopen", answer)
    assert release_script.request_json(GITHUB, token=TEST_CREDENTIAL) == {"ok": True}
    assert received[0].get_header("Authorization") == f"Bearer {TEST_CREDENTIAL}"
    assert received[0].get_header("Accept-encoding") == "identity"
    monkeypatch.setattr(release_script, "MAX_RESPONSE_BYTES", 8)
    with pytest.raises(release_script.ReleaseError, match="too large"):
        release_script.request_json(GITHUB)


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_only_explicit_404_can_mean_missing(
    release_script: ModuleType, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise HTTPError(GITHUB, status, "secret response", Message(), None)

    monkeypatch.setattr(release_script, "urlopen", refuse)
    if status == 404:
        assert release_script.request_json(GITHUB, missing_ok=True) is None
    else:
        with pytest.raises(release_script.RemoteError, match=f"HTTP {status}"):
            release_script.request_json(GITHUB, missing_ok=True)
    with pytest.raises(release_script.RemoteError):
        release_script.request_json(GITHUB)


def test_dns_wait_is_bounded_even_before_a_socket_exists(
    release_script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Event()
    completed = Event()

    def answer(*_args: object, **_kwargs: object) -> io.BytesIO:
        release.wait(timeout=5)
        completed.set()
        return io.BytesIO(b"{}")

    monkeypatch.setattr(release_script, "urlopen", answer)
    monkeypatch.setattr(release_script, "REQUEST_BUDGET", 0.01)
    try:
        with pytest.raises(release_script.ReleaseError, match="timed out"):
            release_script.request_json(GITHUB)
    finally:
        release.set()
        assert completed.wait(timeout=5)


def test_guard_cli_emits_only_action_outputs(
    release_script: ModuleType,
    remote: Remote,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(
        f'[project]\nname="flexi"\nversion="{VERSION}"\n', encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "guard", "--project", str(project)])
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("GH_TOKEN", TEST_CREDENTIAL)

    assert release_script.main() == 0
    output = capsys.readouterr()
    assert output.out == f"version={VERSION}\npublish=true\n"
    assert output.err == ""


def test_cli_failure_does_not_print_credentials(
    release_script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "verify", "--version", "1.2.3rc1"])
    monkeypatch.setenv("GH_TOKEN", "do-not-print-this-secret")
    assert release_script.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "stable X.Y.Z" in output.err
    assert "do-not-print-this-secret" not in output.err
