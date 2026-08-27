"""A release is verified by exactly what a pull request is verified by.

That guarantee used to be structural: one `verify.yaml` holding every job, and
both pipelines calling it. It cost nothing to keep and it made the checks list
twenty-two lines of `verify / …`, with the linter, the suite and the packaging
run all under one name that says none of them.

They are three workflows now, called by both `ci.yaml` and `release.yaml`, and
what was structural is asserted here instead. The failure it exists to prevent
is a real one: `release.yaml` once carried its own narrower copy -- ubuntu only,
two interpreters, no timezone matrix -- so the run that published was the least
tested run in the repository, and nothing said so.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

PIPELINES = ("ci.yaml", "release.yaml")
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


ON = True
"""PyYAML reads a bare `on:` key as the boolean True.

The one thing about this file format worth knowing, and the reason it is named
rather than written as a literal in the middle of an assertion.
"""


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
    """Neither may gain a check the other has not got.

    A check added to CI and not to the release is a check the published
    artefact never had to pass. One added to the release and not to CI is one
    nobody finds out about until they try to release.
    """
    ci, release = (_called(name) for name in PIPELINES)

    assert ci == release, (
        f"only CI runs {ci - release}; only the release runs {release - ci}"
    )


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_every_workflow_a_pipeline_calls_exists_and_is_reusable() -> None:
    """`uses:` is a path, and a wrong one fails at the moment of releasing."""
    for name in _called(PIPELINES[0]):
        assert "workflow_call" in _workflow(name)[ON], f"{name} cannot be called"


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="sdist")
def test_the_gate_waits_for_every_workflow_ci_calls() -> None:
    """`All green` is the one check name the branch ruleset requires.

    It is a gate only if it needs everything. A workflow added to CI and left
    out of its `needs` is a workflow whose failure the ruleset would let
    through, and the name would still say all green.
    """
    jobs = _workflow("ci.yaml")["jobs"]
    calling = {name for name, job in jobs.items() if isinstance(job.get("uses"), str)}

    assert set(jobs["green"]["needs"]) == calling


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
def test_the_pypi_guard_fails_closed_and_has_bounded_network_waits() -> None:
    """Only an authoritative 404 is evidence that a version is unpublished."""
    guard: dict[str, Any] = _workflow("release.yaml")["jobs"]["guard"]
    published = next(step for step in guard["steps"] if step.get("id") == "published")
    script = published["run"]

    assert "--connect-timeout" in script
    assert "--max-time" in script
    assert "--retry" in script
    assert "404)" in script
    assert "*)" in script
    assert "exit 1" in script
