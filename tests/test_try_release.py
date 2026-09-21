"""A local release trial stays bound to one reviewed GitHub run."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import try_release as trial
from scripts.release_probe import ProbeError
from scripts.release_status import Registry, ReleaseError

HEAD = "a" * 40
OTHER = "b" * 40
RUN_ID = 42
VERSION = "0.2.0"
RUN_PATH = f"actions/runs/{RUN_ID}"
RUNS_PATH = "actions/workflows/release.yaml/runs"
EXACT_RUNS = f"{RUNS_PATH}?branch=main&head_sha={HEAD}&per_page=100"
ALL_RUNS = f"{RUNS_PATH}?branch=main&per_page=100"
JOBS_PATH = f"{RUN_PATH}/jobs?filter=latest&per_page=100&page=1"
DISPATCH_PATH = "actions/workflows/release.yaml/dispatches"


def run_response(**changes: object) -> dict[str, object]:
    response: dict[str, object] = {
        "id": RUN_ID,
        "head_sha": HEAD,
        "run_attempt": 1,
        "status": "waiting",
        "path": trial.WORKFLOW,
        "head_branch": "main",
        "event": "push",
        "repository": {"full_name": trial.REPOSITORY},
    }
    response.update(changes)
    return response


def encoded_file(content: str) -> dict[str, str]:
    return {
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode("ascii"),
    }


def verification(
    status: str = "completed", conclusion: str | None = "success"
) -> dict[str, object]:
    return {"name": trial.VERIFY_JOB, "status": status, "conclusion": conclusion}


class Remote(trial.GitHub):
    """Exercise real orchestration with queued API replies and local artifacts."""

    def __init__(self) -> None:
        self.executable = "gh"
        workflow = Path(trial.__file__).resolve().parents[1] / trial.WORKFLOW
        self.responses: dict[str, list[object]] = {
            "git/ref/heads/main": [{"object": {"sha": HEAD}}],
            f"contents/{trial.WORKFLOW}?ref={HEAD}": [
                encoded_file(workflow.read_text(encoding="utf-8"))
            ],
            f"contents/pyproject.toml?ref={HEAD}": [
                encoded_file(f'[project]\nname = "flexi"\nversion = "{VERSION}"\n')
            ],
            EXACT_RUNS: [{"workflow_runs": [run_response()]}],
            ALL_RUNS: [{"workflow_runs": []}],
            DISPATCH_PATH: [{"workflow_run_id": RUN_ID}],
            RUN_PATH: [run_response()],
            JOBS_PATH: [{"jobs": [verification()]}],
        }
        self.requests: list[tuple[str, object]] = []
        self.commands: list[tuple[str, ...]] = []

    def api(self, path: str, *, payload: object = None) -> object:
        self.requests.append((path, payload))
        responses = self.responses[path]
        return responses.pop(0) if len(responses) > 1 else responses[0]

    def command(self, *args: str, payload: object = None) -> str:
        self.commands.append(args)
        assert args[:2] == ("run", "download")
        assert payload is None
        directory = Path(args[args.index("--dir") + 1])
        directory.mkdir(parents=True)
        package = (
            "flexi_test" if args[args.index("--name") + 1] == "test-dist" else "flexi"
        )
        (directory / f"{package}-{VERSION}-py3-none-any.whl").write_bytes(
            b"run artifact"
        )
        return ""


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Remote:
    client = Remote()
    monkeypatch.setattr(trial, "GitHub", lambda: client)
    return client


def test_existing_exact_main_run_is_reused_without_dispatch(remote: Remote) -> None:
    assert remote.select_run(HEAD) == trial.ReleaseRun(RUN_ID, HEAD, 1, "waiting")
    assert remote.requests == [(EXACT_RUNS, None)]


def test_dispatch_returns_the_only_run_that_can_be_selected(remote: Remote) -> None:
    remote.responses[EXACT_RUNS] = [{"workflow_runs": []}]
    remote.responses[RUN_PATH] = [run_response(event="workflow_dispatch")]
    assert remote.select_run(HEAD).id == RUN_ID
    assert remote.requests == [
        (EXACT_RUNS, None),
        (ALL_RUNS, None),
        (DISPATCH_PATH, {"ref": "main", "return_run_details": True}),
        (RUN_PATH, None),
    ]


@pytest.mark.parametrize("identifier", [None, True, 0, -1, "42"])
def test_ambiguous_dispatch_is_not_retried_or_guessed(
    remote: Remote, identifier: object
) -> None:
    remote.responses[EXACT_RUNS] = [{"workflow_runs": []}]
    remote.responses[DISPATCH_PATH] = [{"workflow_run_id": identifier}]
    with pytest.raises(trial.TrialError, match="identifier"):
        remote.select_run(HEAD)
    assert remote.requests[-1] == (
        DISPATCH_PATH,
        {"ref": "main", "return_run_details": True},
    )
    assert sum(path == DISPATCH_PATH for path, _payload in remote.requests) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", {"full_name": "somebody/flexi"}),
        ("repository", None),
        ("head_branch", "dev"),
        ("path", ".github/workflows/ci.yaml"),
        ("event", "pull_request"),
        ("head_sha", "bad-sha"),
        ("head_sha", None),
        ("id", True),
        ("run_attempt", 0),
    ],
)
def test_only_identified_main_release_runs_are_accepted(
    field: str, value: object
) -> None:
    with pytest.raises((trial.TrialError, ReleaseError)):
        trial.ReleaseRun.parse(run_response(**{field: value}))


@pytest.mark.parametrize("dispatch", [False, True])
def test_different_main_sha_cannot_be_selected(remote: Remote, dispatch: bool) -> None:
    if dispatch:
        remote.responses[EXACT_RUNS] = [{"workflow_runs": []}]
        remote.responses[RUN_PATH] = [run_response(head_sha=OTHER)]
    else:
        remote.responses[EXACT_RUNS] = [
            {"workflow_runs": [run_response(head_sha=OTHER)]}
        ]
    with pytest.raises(trial.TrialError, match="main changed"):
        remote.select_run(HEAD)


def test_another_active_release_is_not_duplicated(remote: Remote) -> None:
    remote.responses[EXACT_RUNS] = [{"workflow_runs": []}]
    remote.responses[ALL_RUNS] = [
        {"workflow_runs": [run_response(id=41, head_sha=OTHER)]}
    ]
    with pytest.raises(trial.TrialError, match="Another release is active"):
        remote.select_run(HEAD)
    assert remote.requests == [(EXACT_RUNS, None), (ALL_RUNS, None)]


@pytest.mark.parametrize("status", ["queued", "pending", "requested"])
def test_existing_queued_run_reports_older_release_blocker(
    remote: Remote, status: str
) -> None:
    remote.responses[EXACT_RUNS] = [{"workflow_runs": [run_response(status=status)]}]
    remote.responses[ALL_RUNS] = [
        {"workflow_runs": [run_response(id=41, head_sha=OTHER)]}
    ]
    with pytest.raises(trial.TrialError, match="--run 41"):
        remote.select_run(HEAD)
    assert remote.requests == [(EXACT_RUNS, None), (ALL_RUNS, None)]


def test_queued_run_can_wait_when_previous_release_is_complete(remote: Remote) -> None:
    queued = run_response(status="queued")
    remote.responses[EXACT_RUNS] = [{"workflow_runs": [queued]}]
    remote.responses[ALL_RUNS] = [
        {
            "workflow_runs": [
                queued,
                run_response(id=41, head_sha=OTHER, status="completed"),
            ]
        }
    ]
    assert remote.select_run(HEAD).id == RUN_ID
    assert not any(path == DISPATCH_PATH for path, _payload in remote.requests)


def test_actual_workflow_and_project_are_checked_before_selection(
    remote: Remote,
) -> None:
    assert remote.version(HEAD) == VERSION
    assert remote.requests == [
        (f"contents/{trial.WORKFLOW}?ref={HEAD}", None),
        (f"contents/pyproject.toml?ref={HEAD}", None),
    ]


def test_old_workflow_stops_cli_before_dispatch(
    remote: Remote, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    remote.responses[f"contents/{trial.WORKFLOW}?ref={HEAD}"] = [
        encoded_file("jobs:\n  publish:\n    environment: pypi\n")
    ]
    monkeypatch.setattr(sys, "argv", ["try_release"])
    assert trial.main() == 1
    assert "Merge the reviewed release PR" in capsys.readouterr().err
    assert remote.requests == [
        ("git/ref/heads/main", None),
        (f"contents/{trial.WORKFLOW}?ref={HEAD}", None),
    ]
    assert remote.commands == []


@pytest.mark.parametrize("status", ["waiting", "in_progress", "completed"])
def test_staging_success_does_not_wait_for_production(
    remote: Remote, status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote.responses[RUN_PATH] = [run_response(status=status)]

    def refuse_wait(_seconds: float) -> None:
        pytest.fail("TestPyPI is already verified; production approval must not block")

    monkeypatch.setattr(trial, "sleep", refuse_wait)
    trial.wait_for_testpypi(remote, trial.ReleaseRun(RUN_ID, HEAD, 1, status))


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled", "timed_out"])
def test_unsuccessful_verification_is_not_treated_as_staged(
    remote: Remote, conclusion: str
) -> None:
    remote.responses[JOBS_PATH] = [{"jobs": [verification(conclusion=conclusion)]}]
    with pytest.raises(trial.TrialError, match=f"ended with {conclusion}"):
        trial.wait_for_testpypi(remote, trial.ReleaseRun(RUN_ID, HEAD, 1, "waiting"))


def test_completed_no_op_run_without_verification_is_not_staged(remote: Remote) -> None:
    remote.responses[RUN_PATH] = [run_response(status="completed")]
    remote.responses[JOBS_PATH] = [{"jobs": []}]
    with pytest.raises(trial.TrialError, match="without successful TestPyPI"):
        trial.wait_for_testpypi(remote, trial.ReleaseRun(RUN_ID, HEAD, 1, "waiting"))


def test_duplicate_verification_jobs_are_ambiguous(remote: Remote) -> None:
    remote.responses[JOBS_PATH] = [{"jobs": [verification(), verification()]}]
    with pytest.raises(trial.TrialError, match="ambiguous"):
        trial.wait_for_testpypi(remote, trial.ReleaseRun(RUN_ID, HEAD, 1, "waiting"))


@pytest.mark.parametrize("after_jobs", [False, True])
def test_rerunning_during_verification_requires_explicit_resume(
    remote: Remote, after_jobs: bool
) -> None:
    remote.responses[RUN_PATH] = [
        *([run_response()] if after_jobs else []),
        run_response(run_attempt=2),
    ]
    with pytest.raises(trial.TrialError, match="rerun"):
        trial.wait_for_testpypi(remote, trial.ReleaseRun(RUN_ID, HEAD, 1, "waiting"))


def test_wait_is_bounded_and_does_not_cancel_the_release(
    remote: Remote, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote.responses[JOBS_PATH] = [{"jobs": [verification("queued", None)]}]
    elapsed = 0.0
    waits: list[float] = []

    def now() -> float:
        return elapsed

    def advance(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds
        waits.append(seconds)

    monkeypatch.setattr(trial, "monotonic", now)
    monkeypatch.setattr(trial, "sleep", advance)
    monkeypatch.setattr(trial, "WAIT_SECONDS", 20)
    with pytest.raises(trial.TrialError, match="Timed out"):
        trial.wait_for_testpypi(remote, trial.ReleaseRun(RUN_ID, HEAD, 1, "waiting"))
    assert waits == [10, 10]
    assert all(payload is None for _path, payload in remote.requests)
    assert remote.commands == []


@pytest.mark.parametrize("registry", list(Registry))
def test_artifact_download_is_pinned_to_the_selected_run_and_registry(
    remote: Remote, tmp_path: Path, registry: Registry
) -> None:
    directory = tmp_path / "dist"
    remote.download(trial.ReleaseRun(RUN_ID, HEAD, 1, "waiting"), directory, registry)
    assert remote.commands == [
        (
            "run",
            "download",
            str(RUN_ID),
            "--repo",
            trial.REPOSITORY,
            "--name",
            "test-dist" if registry is Registry.TESTPYPI else "dist",
            "--dir",
            str(directory),
        )
    ]
    assert (
        directory / f"{registry.package.replace('-', '_')}-{VERSION}-py3-none-any.whl"
    ).read_bytes() == b"run artifact"


@pytest.mark.parametrize("during_download", [False, True])
def test_rerun_before_or_during_download_cannot_reach_the_probe(
    remote: Remote, tmp_path: Path, during_download: bool
) -> None:
    remote.responses[RUN_PATH] = [
        *([run_response()] if during_download else []),
        run_response(run_attempt=2),
    ]
    with pytest.raises(trial.TrialError, match="rerun"):
        remote.download(
            trial.ReleaseRun(RUN_ID, HEAD, 1, "waiting"),
            tmp_path / "dist",
            Registry.PYPI,
        )
    assert bool(remote.commands) is during_download


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("fail_probe", [False, True])
def test_cli_probes_both_temporary_artifacts_and_always_cleans_up(
    remote: Remote,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    resume: bool,
    fail_probe: bool,
) -> None:
    artifact_directories: list[Path] = []

    def probe(version: str, production: Path, staging: Path, *, demo: bool) -> None:
        assert version == VERSION
        assert not demo
        for directory, package in ((production, "flexi"), (staging, "flexi_test")):
            assert directory.is_dir()
            assert (
                directory / f"{package}-{VERSION}-py3-none-any.whl"
            ).read_bytes() == b"run artifact"
            artifact_directories.append(directory)
        if fail_probe:
            message = "isolated package smoke failed"
            raise ProbeError(message)

    monkeypatch.setattr(trial, "probe_release", probe)
    monkeypatch.setattr(
        sys, "argv", ["try_release", *(["--run", str(RUN_ID)] if resume else [])]
    )
    assert trial.main() == int(fail_probe)
    assert len(artifact_directories) == 2
    assert artifact_directories[0] != artifact_directories[1]
    assert [args[args.index("--name") + 1] for args in remote.commands] == [
        "dist",
        "test-dist",
    ]
    assert not artifact_directories[0].parent.exists()
    assert not any(path == DISPATCH_PATH for path, _payload in remote.requests)
    if resume:
        assert not any(
            path == "git/ref/heads/main" for path, _payload in remote.requests
        )
    output = capsys.readouterr()
    assert f"--run {RUN_ID}" in output.out
    if fail_probe:
        assert "isolated package smoke failed" in output.err
    else:
        assert "Temporary installations removed" in output.out


@pytest.mark.parametrize(("stdin_tty", "stdout_tty"), [(False, True), (True, False)])
def test_demo_requires_a_terminal_before_any_github_request(
    remote: Remote,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stdin_tty: bool,
    stdout_tty: bool,
) -> None:
    monkeypatch.setattr(sys, "argv", ["try_release", "--demo"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout_tty)
    assert trial.main() == 1
    assert "interactive terminal" in capsys.readouterr().err
    assert remote.requests == []


def test_api_dispatch_uses_the_supported_response_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], object]] = []

    def command(self: trial.GitHub, *args: str, payload: object = None) -> str:
        calls.append((args, payload))
        return json.dumps({"workflow_run_id": RUN_ID})

    monkeypatch.setattr(shutil, "which", lambda _name: "gh")
    monkeypatch.setattr(trial.GitHub, "command", command)
    payload = {"ref": "main", "return_run_details": True}
    assert trial.GitHub().api(DISPATCH_PATH, payload=payload) == {
        "workflow_run_id": RUN_ID
    }
    assert calls == [
        (
            (
                "api",
                "--hostname",
                "github.com",
                f"repos/{trial.REPOSITORY}/{DISPATCH_PATH}",
                "-H",
                "X-GitHub-Api-Version: 2022-11-28",
                "--method",
                "POST",
                "--input",
                "-",
            ),
            payload,
        )
    ]


def test_github_command_fixes_host_and_decodes_utf8_on_every_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def command(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args == (["gh", "api", "example"],)
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["timeout"] == trial.COMMAND_SECONDS
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["GH_HOST"] == "github.com"
        assert environment["GH_PROMPT_DISABLED"] == "1"
        assert "GH_DEBUG" not in environment
        return subprocess.CompletedProcess(["gh"], 0, stdout='{"name": "🌟"}')

    monkeypatch.setattr(shutil, "which", lambda _name: "gh")
    monkeypatch.setattr(subprocess, "run", command)
    monkeypatch.setenv("GH_HOST", "elsewhere.example")
    monkeypatch.setenv("GH_DEBUG", "api")
    assert trial.GitHub().command("api", "example") == '{"name": "🌟"}'
