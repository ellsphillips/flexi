"""Release recovery must preserve published bytes, tags, and existing drafts."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
from collections.abc import Callable
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
RUN_ID = "35540218395"
PREVIEW = f"{VERSION}.dev{RUN_ID}"
SHA = "a" * 40
OTHER_SHA = "b" * 40
REPOSITORY = "example/flexi"
GITHUB = f"https://api.github.com/repos/{REPOSITORY}"
PYPI = f"https://pypi.org/pypi/flexi/{VERSION}/json"
TESTPYPI = f"https://test.pypi.org/pypi/flexi-test/{VERSION}/json"
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


@pytest.fixture
def staging_artifacts(tmp_path: Path) -> Path:
    directory = tmp_path / "staging"
    directory.mkdir()
    (directory / f"flexi_test-{VERSION}-py3-none-any.whl").write_bytes(b"staging wheel")
    (directory / f"flexi_test-{VERSION}.tar.gz").write_bytes(b"staging source archive")
    return directory


class Remote:
    def __init__(
        self, script: ModuleType, artifacts: Path, staging_artifacts: Path
    ) -> None:
        self.script = script
        self.files = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts.iterdir()
        }
        self.test_files = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in staging_artifacts.iterdir()
        }
        self.tag: str | None = None
        self.release = False
        self.writes: list[str] = []
        self.reads: list[str] = []
        self.race: str | None = None
        self.test_version = VERSION

    def request(
        self,
        url: str,
        *,
        token: str = "",
        payload: dict[str, object] | None = None,
        missing_ok: bool = False,
    ) -> object:
        if payload is None:
            self.reads.append(url)
        test_url = f"https://test.pypi.org/pypi/flexi-test/{self.test_version}/json"
        if url in {PYPI, test_url}:
            assert token == "", "GitHub credentials must never reach PyPI"
            files = self.files if url == PYPI else self.test_files
            return {
                "urls": [
                    {"filename": name, "digests": {"sha256": digest}}
                    for name, digest in files.items()
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
    release_script: ModuleType,
    artifacts: Path,
    staging_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Remote:
    remote = Remote(release_script, artifacts, staging_artifacts)
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


@pytest.mark.parametrize("run_id", ["1", RUN_ID, "9" * 20])
def test_preview_versions_identify_the_workflow_run(
    release_script: ModuleType, run_id: str
) -> None:
    version = f"{VERSION}.dev{run_id}"
    assert release_script.preview_version(VERSION, run_id) == version
    assert release_script.validate_staging_version(version, VERSION) == version
    assert release_script.validate_staging_version(VERSION, VERSION) == VERSION


@pytest.mark.parametrize(
    "run_id", ["", "0", "01", "-1", "+1", "1.0", "١", "1\n", "9" * 21]
)
def test_preview_run_ids_must_be_canonical_and_bounded(
    release_script: ModuleType, run_id: str
) -> None:
    with pytest.raises(release_script.ReleaseError, match="GITHUB_RUN_ID"):
        release_script.preview_version(VERSION, run_id)
    with pytest.raises(release_script.ReleaseError, match="staging version"):
        release_script.validate_staging_version(f"{VERSION}.dev{run_id}")


@pytest.mark.parametrize(
    "version", [None, "01.2.3", "1.2.3rc1", "1.2.3.dev1+local", "1.2.3.dev1.dev2"]
)
def test_staging_rejects_other_version_forms(
    release_script: ModuleType, version: object
) -> None:
    with pytest.raises(release_script.ReleaseError, match="staging version"):
        release_script.validate_staging_version(version)


@pytest.mark.parametrize("staging", [VERSION, PREVIEW])
def test_staging_version_must_match_the_production_base(
    release_script: ModuleType, staging: str
) -> None:
    with pytest.raises(release_script.ReleaseError, match="based on production"):
        release_script.validate_staging_version(staging, "1.2.4")


def test_preview_versions_cannot_be_used_as_production_versions(
    release_script: ModuleType, remote: Remote, github: Any
) -> None:
    operations: tuple[Callable[[], object], ...] = (
        lambda: release_script.validate_version(PREVIEW),
        lambda: release_script.preview_version(PREVIEW, RUN_ID),
        lambda: release_script.validate_staging_version(PREVIEW, PREVIEW),
        lambda: release_script.filenames(PREVIEW),
        lambda: release_script.published_files(PREVIEW),
        lambda: github.tag_sha(PREVIEW),
        lambda: github.has_release(PREVIEW),
    )
    for operation in operations:
        with pytest.raises(release_script.ReleaseError, match=r"stable X\.Y\.Z"):
            operation()
    assert remote.reads == []


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
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
    monkeypatch.setenv("GH_TOKEN", TEST_CREDENTIAL)

    assert release_script.main() == 0
    output = capsys.readouterr()
    assert output.out == (f"version={VERSION}\ntest_version={PREVIEW}\npublish=true\n")
    assert output.err == ""


def test_guard_requires_a_valid_workflow_run_before_remote_reads(
    release_script: ModuleType,
    remote: Remote,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(f'[project]\nname="flexi"\nversion="{VERSION}"\n')
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "guard", "--project", str(project)])
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)

    assert release_script.main() == 1
    output = capsys.readouterr()
    assert "GITHUB_RUN_ID" in output.err
    assert output.out == ""
    assert remote.reads == []


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


@pytest.mark.parametrize("registry", ["pypi", "testpypi"])
@pytest.mark.parametrize("file_count", [0, 1, 2])
@pytest.mark.parametrize("complete", [False, True])
def test_verification_uses_only_the_selected_registry(
    release_script: ModuleType,
    remote: Remote,
    artifacts: Path,
    staging_artifacts: Path,
    registry: str,
    file_count: int,
    complete: bool,
) -> None:
    artifacts = artifacts if registry == "pypi" else staging_artifacts
    selected = remote.files if registry == "pypi" else remote.test_files
    unselected = remote.test_files if registry == "pypi" else remote.files
    for name in list(selected)[file_count:]:
        del selected[name]
    for name in unselected:
        unselected[name] = "0" * 64
    target = release_script.Registry(registry)

    if complete and file_count < 2:
        with pytest.raises(
            release_script.ReleaseError, match=rf"{target.label}.*both distributions"
        ):
            release_script.verify_artifacts(
                artifacts, VERSION, registry=target, complete=complete
            )
    else:
        release_script.verify_artifacts(
            artifacts, VERSION, registry=target, complete=complete
        )

    assert remote.reads == [PYPI if registry == "pypi" else TESTPYPI]
    assert remote.writes == []


@pytest.mark.parametrize("registry", ["pypi", "testpypi"])
@pytest.mark.parametrize("complete", [False, True])
def test_hash_mismatch_in_either_registry_is_refused(
    release_script: ModuleType,
    remote: Remote,
    artifacts: Path,
    staging_artifacts: Path,
    registry: str,
    complete: bool,
) -> None:
    artifacts = artifacts if registry == "pypi" else staging_artifacts
    selected = remote.files if registry == "pypi" else remote.test_files
    selected[next(iter(selected))] = "0" * 64
    with pytest.raises(release_script.ReleaseError, match="differs from the tested"):
        release_script.verify_artifacts(
            artifacts,
            VERSION,
            registry=release_script.Registry(registry),
            complete=complete,
        )
    assert remote.writes == []


def test_testpypi_cannot_complete_a_production_release(
    release_script: ModuleType, remote: Remote, artifacts: Path, github: Any
) -> None:
    remote.files.clear()
    remote.tag = SHA
    remote.release = True

    assert release_script.needs_publication(VERSION, SHA, github)
    with pytest.raises(release_script.ReleaseError, match=r"PyPI.*both distributions"):
        release_script.finalize(artifacts, VERSION, SHA, github)
    assert TESTPYPI not in remote.reads
    assert remote.writes == []


@pytest.mark.parametrize("registry", ["pypi", "testpypi"])
@pytest.mark.parametrize("complete", [False, True])
def test_verify_cli_honors_registry_and_completeness(
    release_script: ModuleType,
    remote: Remote,
    artifacts: Path,
    staging_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registry: str,
    complete: bool,
) -> None:
    artifacts = artifacts if registry == "pypi" else staging_artifacts
    selected = remote.files if registry == "pypi" else remote.test_files
    selected.pop(next(iter(selected)))
    arguments = [
        str(SCRIPT),
        "verify",
        "--version",
        VERSION,
        "--dist",
        str(artifacts),
        "--registry",
        registry,
    ]
    if complete:
        arguments.append("--complete")
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("GH_TOKEN", TEST_CREDENTIAL)

    assert release_script.main() == int(complete)
    output = capsys.readouterr()
    assert output.out == ""
    assert ("both distributions" in output.err) is complete
    assert remote.reads[-1] == (PYPI if registry == "pypi" else TESTPYPI)
    assert remote.writes == []


@pytest.mark.parametrize("tag", [SHA, OTHER_SHA])
def test_preview_cli_verifies_staging_bytes_against_the_stable_production_tag(
    release_script: ModuleType,
    remote: Remote,
    staging_artifacts: Path,
    monkeypatch: pytest.MonkeyPatch,
    tag: str,
) -> None:
    remote.tag = tag
    remote.test_version = PREVIEW
    remote.test_files = {
        name.replace(VERSION, PREVIEW): digest
        for name, digest in remote.test_files.items()
    }
    for path in staging_artifacts.iterdir():
        path.rename(path.with_name(path.name.replace(VERSION, PREVIEW)))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("GH_TOKEN", TEST_CREDENTIAL)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "verify",
            "--version",
            PREVIEW,
            "--registry",
            "testpypi",
            "--dist",
            str(staging_artifacts),
            "--complete",
        ],
    )

    assert release_script.main() == int(tag != SHA)
    assert remote.reads[0] == f"{GITHUB}/git/ref/tags/v{VERSION}"
    if tag == SHA:
        assert remote.reads[-1] == (
            f"https://test.pypi.org/pypi/flexi-test/{PREVIEW}/json"
        )
    else:
        assert len(remote.reads) == 1
    assert remote.writes == []


@pytest.mark.parametrize("command", ["verify", "finalize"])
def test_preview_cli_cannot_publish_or_tag_a_production_preview(
    release_script: ModuleType,
    remote: Remote,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), command, "--version", PREVIEW])
    assert release_script.main() == 1
    assert remote.reads == []
    assert remote.writes == []


@pytest.mark.parametrize(
    "registry", ["https://evil.example", "pypi/../evil", "TESTPYPI", "testpypi\n"]
)
def test_registry_must_be_a_fixed_known_selection(
    release_script: ModuleType,
    remote: Remote,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "verify", "--registry", registry])
    with pytest.raises(SystemExit, match="2"):
        release_script.main()
    assert remote.reads == []
    assert remote.writes == []


@pytest.mark.parametrize("command", ["guard", "finalize"])
@pytest.mark.parametrize("options", [("--registry", "testpypi"), ("--complete",)])
def test_registry_and_completeness_options_cannot_change_production_commands(
    release_script: ModuleType,
    remote: Remote,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    options: tuple[str, ...],
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), command, *options])
    with pytest.raises(SystemExit, match="2"):
        release_script.main()
    assert remote.reads == []
    assert remote.writes == []


class PublicationClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def publication_clock(
    release_script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> PublicationClock:
    clock = PublicationClock()
    monkeypatch.setattr(release_script, "monotonic", clock.monotonic)
    monkeypatch.setattr(release_script, "sleep", clock.sleep)
    return clock


def test_complete_cli_waits_for_registry_propagation(
    release_script: ModuleType,
    remote: Remote,
    staging_artifacts: Path,
    publication_clock: PublicationClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete_files = dict(remote.test_files)
    remote.test_files.clear()

    def publish(seconds: float) -> None:
        publication_clock.sleep(seconds)
        remote.test_files.update(complete_files)

    monkeypatch.setattr(release_script, "sleep", publish)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("GH_TOKEN", TEST_CREDENTIAL)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "verify",
            "--version",
            VERSION,
            "--dist",
            str(staging_artifacts),
            "--registry",
            "testpypi",
            "--complete",
            "--wait",
            "120",
        ],
    )

    assert release_script.main() == 0
    assert publication_clock.sleeps == [5.0]
    assert remote.reads.count(TESTPYPI) == 2
    assert remote.writes == []


@pytest.mark.parametrize(
    ("seconds", "sleeps", "requests"), [(0, [], 1), (3, [3.0], 1), (7, [5.0, 2.0], 2)]
)
def test_missing_publication_stops_at_the_deadline(
    release_script: ModuleType,
    remote: Remote,
    staging_artifacts: Path,
    publication_clock: PublicationClock,
    seconds: int,
    sleeps: list[float],
    requests: int,
) -> None:
    remote.test_files.clear()

    with pytest.raises(release_script.PublicationPendingError):
        release_script.wait_for_publication(
            staging_artifacts,
            VERSION,
            registry=release_script.Registry.TESTPYPI,
            seconds=seconds,
        )

    assert publication_clock.sleeps == sleeps
    assert publication_clock.now == seconds
    assert remote.reads == [TESTPYPI] * requests


@pytest.mark.parametrize("registry", ["pypi", "testpypi"])
def test_partial_upload_with_conflicting_bytes_is_never_retried(
    release_script: ModuleType,
    remote: Remote,
    artifacts: Path,
    staging_artifacts: Path,
    publication_clock: PublicationClock,
    registry: str,
) -> None:
    artifacts = artifacts if registry == "pypi" else staging_artifacts
    selected = remote.files if registry == "pypi" else remote.test_files
    selected.pop(next(iter(selected)))
    selected[next(iter(selected))] = "0" * 64

    with pytest.raises(release_script.ReleaseError, match="differs from the tested"):
        release_script.wait_for_publication(
            artifacts, VERSION, registry=release_script.Registry(registry), seconds=120
        )

    assert publication_clock.sleeps == []
    assert remote.reads == [PYPI if registry == "pypi" else TESTPYPI]


def test_registry_errors_are_never_retried(
    release_script: ModuleType,
    staging_artifacts: Path,
    publication_clock: PublicationClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    def unavailable(url: str, **_kwargs: object) -> None:
        requests.append(url)
        raise release_script.RemoteError(503)

    monkeypatch.setattr(release_script, "request_json", unavailable)
    with pytest.raises(release_script.RemoteError, match="HTTP 503"):
        release_script.wait_for_publication(
            staging_artifacts,
            VERSION,
            registry=release_script.Registry.TESTPYPI,
            seconds=120,
        )

    assert requests == [TESTPYPI]
    assert publication_clock.sleeps == []


@pytest.mark.parametrize(
    "arguments",
    [
        ("verify", "--complete", "--wait", "-1"),
        ("verify", "--complete", "--wait", "121"),
        ("verify", "--complete", "--wait", "inf"),
        ("verify", "--wait", "120"),
        ("verify", "--wait", "0"),
        ("guard", "--wait", "0"),
        ("finalize", "--wait", "120"),
    ],
)
def test_invalid_wait_options_are_rejected_before_network_access(
    release_script: ModuleType,
    remote: Remote,
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *arguments])
    with pytest.raises(SystemExit, match="2"):
        release_script.main()
    assert remote.reads == []
    assert remote.writes == []


@pytest.mark.parametrize("registry", ["pypi", "testpypi"])
def test_registry_distribution_names_are_distinct(
    release_script: ModuleType, registry: str
) -> None:
    target = release_script.Registry(registry)
    package = "flexi" if registry == "pypi" else "flexi-test"
    normalized = package.replace("-", "_")
    assert target.package == package
    assert release_script.filenames(VERSION, target) == {
        f"{normalized}-{VERSION}-py3-none-any.whl",
        f"{normalized}-{VERSION}.tar.gz",
    }
    assert release_script.filenames(VERSION) == release_script.filenames(
        VERSION, release_script.Registry.PYPI
    )


@pytest.mark.parametrize("registry", ["pypi", "testpypi"])
def test_other_registry_artifacts_are_rejected_before_network(
    release_script: ModuleType,
    remote: Remote,
    artifacts: Path,
    staging_artifacts: Path,
    registry: str,
) -> None:
    wrong_directory = staging_artifacts if registry == "pypi" else artifacts
    with pytest.raises(release_script.ReleaseError, match="exactly"):
        release_script.verify_artifacts(
            wrong_directory, VERSION, registry=release_script.Registry(registry)
        )
    assert remote.reads == []
