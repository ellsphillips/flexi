"""A release is verified by exactly what a pull request is verified by.

`ci.yaml` and `release.yaml` call the same three reusable workflows, and
nothing in GitHub Actions holds them to it. A release pipeline that drifts
narrower publishes the least tested run in the repository, silently.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

PIPELINES = ("ci.yaml", "release.yaml")
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


ON = True
"""PyYAML reads a bare `on:` key as the boolean True."""


def _workflow(name: str) -> dict[Any, Any]:
    loaded: dict[Any, Any] = yaml.safe_load(
        (WORKFLOWS / name).read_text(encoding="utf-8")
    )
    return loaded


def _called(name: str) -> set[str]:
    """The reusable workflows a pipeline calls, by filename."""
    jobs: dict[str, Any] = _workflow(name)["jobs"]
    return {
        Path(job["uses"]).name
        for job in jobs.values()
        if isinstance(job.get("uses"), str)
    }


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_both_pipelines_verify_with_the_same_workflows() -> None:
    """A check in only one of them is one the other never has to pass."""
    ci, release = (_called(name) for name in PIPELINES)

    assert ci == release, (
        f"only CI runs {ci - release}; only the release runs {release - ci}"
    )


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_called_workflows_exist_and_are_reusable() -> None:
    """`uses:` is a path, and a wrong one fails at the moment of releasing."""
    for name in _called(PIPELINES[0]):
        assert "workflow_call" in _workflow(name)[ON], f"{name} cannot be called"


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_gate_waits_for_every_workflow_ci_calls() -> None:
    """`All green` is the one check name the branch ruleset requires.

    A workflow left out of its `needs` is one whose failure the ruleset lets
    through under a name that says all green.
    """
    jobs = _workflow("ci.yaml")["jobs"]
    calling = {name for name, job in jobs.items() if isinstance(job.get("uses"), str)}

    assert set(jobs["green"]["needs"]) == calling


def _needs(job: dict[str, Any]) -> set[str]:
    """What a job waits for. YAML allows one name or a list of them."""
    required = job.get("needs", [])
    return {required} if isinstance(required, str) else set(required)


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_green_reads_a_result_for_every_job_it_needs() -> None:
    """Waiting for a check is not the same as reading what it said.

    `always()` runs the gate whatever happened, so a job in `needs` and out of
    `RESULTS` can fail while `All green` passes.
    """
    jobs = _workflow("ci.yaml")["jobs"]
    results = jobs["green"]["steps"][0]["env"]["RESULTS"]

    for name in _needs(jobs["green"]):
        assert f"needs.{name}.result" in results, f"the gate never reads {name}"


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_publishing_waits_for_every_check_the_release_calls() -> None:
    """`artefact.needs` gates the release, not the list of workflows called.

    A fourth check added to both pipelines satisfies the two tests above and
    changes nothing here, so the release would ship while it was still running.
    """
    jobs = _workflow("release.yaml")["jobs"]
    calling = {name for name, job in jobs.items() if isinstance(job.get("uses"), str)}

    assert calling | {"guard"} <= _needs(jobs["artefact"])
    assert "artefact" in _needs(jobs["publish"])
    assert "publish" in _needs(jobs["tag"])


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_every_check_the_release_calls_is_behind_the_guard() -> None:
    """A push to main that changes no version runs the guard and stops.

    A check without the condition runs the whole matrix on every README fix,
    and one that does not wait for the guard cannot read it.
    """
    jobs = _workflow("release.yaml")["jobs"]

    for name, job in jobs.items():
        if not isinstance(job.get("uses"), str):
            continue
        assert "guard" in _needs(job), f"{name} cannot read the guard"
        assert job.get("if") == "needs.guard.outputs.publish == 'true'", name


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_third_party_actions_are_pinned_to_reviewable_commits() -> None:
    """A mutable tag cannot change the code a trusted workflow executes."""
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        jobs: dict[str, Any] = _workflow(path.name)["jobs"]
        for job in jobs.values():
            for step in job.get("steps", []):
                used = step.get("uses")
                if not isinstance(used, str) or used.startswith("./"):
                    continue
                assert PINNED_ACTION.fullmatch(used), (
                    f"{path.name} executes mutable action reference {used!r}"
                )


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_release_guard_uses_the_tested_state_machine() -> None:
    guard: dict[str, Any] = _workflow("release.yaml")["jobs"]["guard"]
    assert '"$GITHUB_REF" != "refs/heads/main"' in guard["steps"][0]["run"]
    status = next(step for step in guard["steps"] if step.get("id") == "status")
    assert 'scripts/release_status.py guard >> "$GITHUB_OUTPUT"' in status["run"]
    assert guard["outputs"] == {
        "version": "${{ steps.status.outputs.version }}",
        "publish": "${{ steps.status.outputs.publish }}",
    }


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_publishing_never_executes_repository_code_with_oidc() -> None:
    publish = _workflow("release.yaml")["jobs"]["publish"]
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
    assert publish["environment"]["name"] == "pypi"
    for step in publish["steps"]:
        assert not step.get("uses", "").startswith("actions/checkout@")
        if "run" in step:
            assert (
                step["run"].strip().startswith("uv publish --trusted-publishing always")
            )


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_release_preparation_separates_code_execution_from_write_credentials() -> None:
    workflow = _workflow("release-prepare.yaml")
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    jobs = workflow["jobs"]
    assert jobs["prepare"]["permissions"] == {"contents": "read"}
    for step in jobs["prepare"]["steps"]:
        assert "secrets." not in str(step)
        assert "id-token" not in str(step)
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    for step in jobs["commit"]["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["ref"] == "${{ github.sha }}"
            assert step["with"]["persist-credentials"] is False
        if step.get("uses", "").startswith("actions/create-github-app-token@"):
            assert step["if"] == "steps.review.outputs.changed == 'true'"
            assert step["with"]["permission-contents"] == "write"
            assert step["with"]["permission-pull-requests"] == "read"
            assert step["with"]["repositories"] == "${{ github.event.repository.name }}"


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_title_edits_revalidate_and_reprepare_the_release() -> None:
    check = _workflow("release-check.yaml")
    prepare = _workflow("release-prepare.yaml")
    for workflow, event in ((check, "pull_request"), (prepare, "pull_request_target")):
        trigger = workflow[ON][event]
        assert trigger["branches"] == ["main"]
        assert {"opened", "edited", "synchronize", "reopened"} <= set(trigger["types"])
    assert check["jobs"]["prepared"]["name"] == "Release prepared"
    assert check["permissions"] == {"contents": "read", "pull-requests": "read"}


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_preparer_limits_automatic_and_bootstrap_sources() -> None:
    jobs = _workflow("release-prepare.yaml")["jobs"]
    assert "github.ref == 'refs/heads/dev'" in jobs["plan"]["if"]
    assert "head.repo.full_name == github.repository" in jobs["plan"]["if"]
    assert "head.ref == 'dev'" in jobs["plan"]["if"]
    assert set(jobs["commit"]["needs"]) == {"plan", "prepare"}


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_ci_uv_is_pinned_and_supported() -> None:
    versions: set[str | None] = set()
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        for job in _workflow(path.name)["jobs"].values():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                    versions.add(step.get("with", {}).get("version"))

    assert len(versions) == 1, "all CI jobs must use the same uv version"
    pinned = versions.pop()
    assert pinned is not None, "CI must pin uv independently of local setup"
    project = tomllib.loads(
        (WORKFLOWS.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert Version(pinned) in SpecifierSet(project["tool"]["uv"]["required-version"])
