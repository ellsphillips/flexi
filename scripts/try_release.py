"""Stage a reviewed release and try its TestPyPI wheel locally."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep

import yaml

from scripts.release_probe import ProbeError, probe_release
from scripts.release_status import (
    Registry,
    ReleaseError,
    validate_sha,
    validate_version,
)

REPOSITORY = "ellsphillips/flexi"
WORKFLOW = ".github/workflows/release.yaml"
VERIFY_JOB = "Verify the TestPyPI release"
POLL_SECONDS = 10
WAIT_SECONDS = 90 * 60
COMMAND_SECONDS = 120
PAGE_SIZE = 100


class TrialError(Exception):
    """The selected release cannot be staged or tested safely."""


def fields(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        message = "GitHub returned an invalid object"
        raise TrialError(message)
    return dict(value)


def records(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        message = "GitHub returned an invalid list"
        raise TrialError(message)
    return [fields(item) for item in value]


def positive_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        message = "GitHub returned an invalid run identifier"
        raise TrialError(message)
    return value


@dataclass(frozen=True)
class ReleaseRun:
    id: int
    sha: str
    attempt: int
    status: str

    @classmethod
    def parse(cls, value: object) -> ReleaseRun:
        data = fields(value)
        commit = data.get("head_sha")
        status = data.get("status")
        if (
            data.get("path") != WORKFLOW
            or data.get("head_branch") != "main"
            or data.get("event") not in {"push", "workflow_dispatch"}
            or fields(data.get("repository")).get("full_name") != REPOSITORY
            or not isinstance(commit, str)
            or not isinstance(status, str)
        ):
            message = "Expected this repository's release.yaml run from main"
            raise TrialError(message)
        return cls(
            positive_id(data.get("id")),
            validate_sha(commit),
            positive_id(data.get("run_attempt")),
            status,
        )

    @property
    def url(self) -> str:
        return f"https://github.com/{REPOSITORY}/actions/runs/{self.id}"

    def require_same_attempt(self, other: ReleaseRun) -> None:
        if (self.id, self.sha, self.attempt) != (other.id, other.sha, other.attempt):
            message = "The release was rerun while testing; resume with --run"
            raise TrialError(message)


class GitHub:
    def __init__(self) -> None:
        executable = shutil.which("gh")
        if executable is None:
            message = "Install the GitHub CLI (gh), then sign in with gh auth login"
            raise TrialError(message)
        self.executable = executable

    def command(self, *args: str, payload: object = None) -> str:
        environment = dict(os.environ)
        # Explicit host/repository selection must win over local CLI defaults.
        environment.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1")
        environment.pop("GH_DEBUG", None)
        try:
            result = subprocess.run(  # noqa: S603 - executable located on PATH
                [self.executable, *args],
                input=None if payload is None else json.dumps(payload),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=COMMAND_SECONDS,
                env=environment,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            message = "GitHub CLI failed or timed out; check the run before retrying"
            raise TrialError(message) from error
        if result.returncode:
            message = (
                "GitHub CLI failed; check gh auth status and repository access. "
                "For an existing run, inspect its Actions page before retrying."
            )
            raise TrialError(message)
        return result.stdout

    def api(self, path: str, *, payload: object = None) -> object:
        args = [
            "api",
            "--hostname",
            "github.com",
            f"repos/{REPOSITORY}/{path}",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
        ]
        if payload is not None:
            args += ["--method", "POST", "--input", "-"]
        try:
            return json.loads(self.command(*args, payload=payload))
        except ValueError as error:
            message = "GitHub did not return run details; inspect Actions and use --run"
            raise TrialError(message) from error

    def main_sha(self) -> str:
        data = fields(self.api("git/ref/heads/main"))
        commit = fields(data.get("object")).get("sha")
        return validate_sha(commit if isinstance(commit, str) else "")

    def file(self, name: str, sha: str) -> str:
        data = fields(self.api(f"contents/{name}?ref={sha}"))
        encoded = data.get("content")
        if data.get("encoding") != "base64" or not isinstance(encoded, str):
            message = f"Cannot read {name} from the release commit"
            raise TrialError(message)
        try:
            return base64.b64decode("".join(encoded.split()), validate=True).decode()
        except ValueError as error:
            message = f"Invalid content for {name} in the release commit"
            raise TrialError(message) from error

    def version(self, sha: str) -> str:
        require_staging_workflow(self.file(WORKFLOW, sha))
        project = fields(tomllib.loads(self.file("pyproject.toml", sha)).get("project"))
        if project.get("name") != "flexi":
            message = "The release commit does not contain the flexi project"
            raise TrialError(message)
        return validate_version(project.get("version"))

    def run(self, run_id: int) -> ReleaseRun:
        return ReleaseRun.parse(self.api(f"actions/runs/{run_id}"))

    def select_run(self, sha: str) -> ReleaseRun:
        path = "actions/workflows/release.yaml/runs"
        response = fields(self.api(f"{path}?branch=main&head_sha={sha}&per_page=100"))
        runs = [
            ReleaseRun.parse(item) for item in records(response.get("workflow_runs"))
        ]
        if runs:
            selected = max(runs, key=lambda run: run.id)
            if selected.status in {"queued", "pending", "requested"}:
                self.require_available_slot(before=selected.id)
        else:
            self.require_available_slot()
            print("Starting the release workflow on main…", flush=True)
            dispatched = fields(
                self.api(
                    "actions/workflows/release.yaml/dispatches",
                    payload={"ref": "main", "return_run_details": True},
                )
            )
            selected = self.run(positive_id(dispatched.get("workflow_run_id")))
        if selected.sha != sha:
            message = "main changed during selection; inspect Actions before retrying"
            raise TrialError(message)
        return selected

    def require_available_slot(self, *, before: int | None = None) -> None:
        # An approval-blocked release holds the workflow's concurrency group.
        response = fields(
            self.api("actions/workflows/release.yaml/runs?branch=main&per_page=100")
        )
        for item in records(response.get("workflow_runs")):
            active = ReleaseRun.parse(item)
            if active.status != "completed" and (before is None or active.id < before):
                message = (
                    f"Another release is active: {active.url}\n"
                    f"Try it with --run {active.id}, or finish it in GitHub first."
                )
                raise TrialError(message)

    def jobs(self, run: ReleaseRun) -> list[dict[str, object]]:
        jobs = []
        for page in range(1, 11):
            # latest includes successful jobs retained by a failed-jobs rerun.
            data = fields(
                self.api(
                    f"actions/runs/{run.id}/jobs?filter=latest&per_page=100&page={page}"
                )
            )
            batch = records(data.get("jobs"))
            jobs.extend(batch)
            if len(batch) < PAGE_SIZE:
                return jobs
        message = "The release returned too many jobs"
        raise TrialError(message)

    def download(self, run: ReleaseRun, directory: Path, registry: Registry) -> None:
        run.require_same_attempt(self.run(run.id))
        self.command(
            "run",
            "download",
            str(run.id),
            "--repo",
            REPOSITORY,
            "--name",
            "test-dist" if registry is Registry.TESTPYPI else "dist",
            "--dir",
            str(directory),
        )
        run.require_same_attempt(self.run(run.id))


def require_staging_workflow(source: str) -> None:
    try:
        workflow = yaml.safe_load(source)
        jobs = fields(workflow.get("jobs") if isinstance(workflow, dict) else None)
        stage = fields(jobs.get("test-publish"))
        verify = fields(jobs.get("test-verify"))
        publish = fields(jobs.get("publish"))
        needs = publish.get("needs")
        environment = publish.get("environment")
        staging_environment = stage.get("environment")
        if isinstance(environment, dict):
            environment = environment.get("name")
        if isinstance(staging_environment, dict):
            staging_environment = staging_environment.get("name")
        valid = (
            staging_environment == "testpypi"
            and any(
                str(step.get("uses", "")).startswith("actions/download-artifact@")
                and step.get("with") == {"name": "test-dist", "path": "dist"}
                for step in records(stage.get("steps"))
            )
            and verify.get("name") == VERIFY_JOB
            and isinstance(needs, list)
            and "test-verify" in needs
            and environment == "pypi"
        )
    except (TrialError, yaml.YAMLError):
        valid = False
    if not valid:
        message = (
            "main does not contain the TestPyPI release workflow yet. "
            "Merge the reviewed release PR before running this command."
        )
        raise TrialError(message)


def wait_for_testpypi(github: GitHub, run: ReleaseRun) -> None:
    deadline = monotonic() + WAIT_SECONDS
    previous = ""
    while monotonic() < deadline:
        current = github.run(run.id)
        run.require_same_attempt(current)
        matches = [job for job in github.jobs(run) if job.get("name") == VERIFY_JOB]
        if len(matches) > 1:
            message = "The run has ambiguous TestPyPI verification jobs"
            raise TrialError(message)
        job = matches[0] if matches else {}
        state = str(job.get("status", "waiting for release checks"))
        if state == "completed":
            conclusion = job.get("conclusion")
            if conclusion == "success":
                run.require_same_attempt(github.run(run.id))
                return
            message = (
                f"TestPyPI verification ended with {conclusion}. "
                "Inspect the run and rerun failed jobs, or use --run with the "
                "original staged release if publication was skipped."
            )
            raise TrialError(message)
        if current.status == "completed":
            message = "The run finished without successful TestPyPI verification"
            raise TrialError(message)
        if state != previous:
            print(f"TestPyPI: {state}…", flush=True)
            previous = state
        sleep(POLL_SECONDS)
    message = "Timed out waiting for TestPyPI; the GitHub workflow is still running"
    raise TrialError(message)


def require_terminal() -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        message = "Use --demo in an interactive terminal"
        raise TrialError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", type=int, metavar="RUN_ID", help="resume a specific release run ID"
    )
    parser.add_argument(
        "--demo", action="store_true", help="open the staged app after checks"
    )
    args = parser.parse_args()
    try:
        if args.demo:
            require_terminal()
        github = GitHub()
        if args.run is None:
            sha = github.main_sha()
            version = github.version(sha)
            print(f"Release: flexi {version} from main ({sha[:12]})", flush=True)
            run = github.select_run(sha)
        else:
            run = github.run(positive_id(args.run))
            version = github.version(run.sha)
            print(f"Release: flexi {version} ({run.sha[:12]})", flush=True)
        print(
            f"{run.url}\nResume: uv run -m scripts.try_release --run {run.id}"
            + (" --demo" if args.demo else ""),
            flush=True,
        )
        wait_for_testpypi(github, run)
        with tempfile.TemporaryDirectory(prefix="flexi-release-") as temporary:
            production = Path(temporary) / "dist"
            staging = Path(temporary) / "test-dist"
            github.download(run, production, Registry.PYPI)
            github.download(run, staging, Registry.TESTPYPI)
            probe_release(version, production, staging, demo=args.demo)
        print(
            f"Passed: flexi-test and flexi {version}. Temporary installations removed."
        )
        print("Production approval remains yours in GitHub Actions.")
    except KeyboardInterrupt:
        print(
            "\nStopped locally; the GitHub workflow was not cancelled.", file=sys.stderr
        )
        return 130
    except (TrialError, ReleaseError, ProbeError, OSError, ValueError) as error:
        print(f"Release trial stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
