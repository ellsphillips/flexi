"""Check and finish a release without replacing published files or existing tags."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PACKAGE = "flexi"
STABLE_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", re.ASCII)
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
REQUEST_TIMEOUT = 15.0
REQUEST_BUDGET = 30.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RELEASE_PAGES = 10
RELEASES_PER_PAGE = 100
MAX_PUBLICATION_WAIT = 120
PUBLICATION_POLL_INTERVAL = 5.0


class Registry(StrEnum):
    PYPI = "pypi"
    TESTPYPI = "testpypi"

    @property
    def package(self) -> str:
        return "flexi" if self is Registry.PYPI else "flexi-test"

    @property
    def root(self) -> str:
        return {
            Registry.PYPI: "https://pypi.org",
            Registry.TESTPYPI: "https://test.pypi.org",
        }[self]

    @property
    def label(self) -> str:
        return "PyPI" if self is Registry.PYPI else "TestPyPI"


class ReleaseError(Exception):
    """A release cannot proceed safely; the caller must leave remote state alone."""


class PublicationPendingError(ReleaseError):
    """The registry has not exposed both verified distribution files yet."""


class RemoteError(ReleaseError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"Release metadata request failed with HTTP {status}")


def read_json(request: Request, *, missing_ok: bool) -> object:
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        error.close()
        if missing_ok and error.code == HTTPStatus.NOT_FOUND:
            return None
        raise RemoteError(error.code) from error
    if len(body) > MAX_RESPONSE_BYTES:
        message = "Release metadata response is too large"
        raise ReleaseError(message)
    return json.loads(body)


def request_json(
    url: str,
    *,
    token: str = "",
    payload: dict[str, object] | None = None,
    missing_ok: bool = False,
) -> object:
    """Read bounded JSON, or fail without printing credentials or response bodies.

    The daemon deadline also bounds DNS resolution, which socket timeouts do
    not cover. The command exits on a timeout; it never retries a remote write
    whose outcome is uncertain.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "Accept-Encoding": "identity",
        "User-Agent": "flexi-release",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(  # noqa: S310 - fixed HTTPS roots and validated path values
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
    )
    results: list[object] = []
    errors: list[Exception] = []

    def read() -> None:
        try:
            results.append(read_json(request, missing_ok=missing_ok))
        except (OSError, ValueError, ReleaseError) as error:
            errors.append(error)

    worker = Thread(target=read, daemon=True)
    worker.start()
    worker.join(REQUEST_BUDGET)
    if worker.is_alive():
        message = "Release metadata request timed out; no further changes attempted"
        raise ReleaseError(message)
    if errors:
        if isinstance(errors[0], ReleaseError):
            raise errors[0]
        message = "Could not read valid release metadata; no further changes attempted"
        raise ReleaseError(message) from errors[0]
    return results[0]


def validate_version(version: object) -> str:
    if not isinstance(version, str) or STABLE_VERSION.fullmatch(version) is None:
        message = "The release version must be a stable X.Y.Z without leading zeroes"
        raise ReleaseError(message)
    return version


def validate_sha(sha: str) -> str:
    if COMMIT_SHA.fullmatch(sha) is None:
        message = "GITHUB_SHA must be the full commit being released"
        raise ReleaseError(message)
    return sha


def project_version(project: Path) -> str:
    with project.open("rb") as source:
        metadata = tomllib.load(source)["project"]
    if metadata.get("name") != PACKAGE:
        message = "This release workflow only publishes flexi"
        raise ReleaseError(message)
    return validate_version(metadata.get("version"))


def filenames(version: str, registry: Registry = Registry.PYPI) -> set[str]:
    distribution = registry.package.replace("-", "_")
    return {
        f"{distribution}-{version}-py3-none-any.whl",
        f"{distribution}-{version}.tar.gz",
    }


def published_files(version: str, registry: Registry = Registry.PYPI) -> dict[str, str]:
    payload = request_json(
        f"{registry.root}/pypi/{registry.package}/{version}/json", missing_ok=True
    )
    if payload is None:
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get("urls"), list):
        message = f"{registry.label} returned invalid release metadata"
        raise ReleaseError(message)
    found: dict[str, str] = {}
    for item in payload["urls"]:
        name = item.get("filename") if isinstance(item, dict) else None
        if not isinstance(name, str) or name not in filenames(version, registry):
            message = (
                f"{registry.label} has unexpected distribution files; "
                "refusing this release"
            )
            raise ReleaseError(message)
        digests = item.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            message = (
                f"{registry.label} did not provide a valid SHA256 for a published file"
            )
            raise ReleaseError(message)
        if name in found:
            message = f"{registry.label} returned duplicate distribution filenames"
            raise ReleaseError(message)
        found[name] = digest
    return found


class GitHub:
    def __init__(self, repository: str, token: str) -> None:
        if REPOSITORY.fullmatch(repository) is None:
            message = "GITHUB_REPOSITORY must identify one owner/repository"
            raise ReleaseError(message)
        self.root = f"https://api.github.com/repos/{repository}"
        self.token = token

    def api(
        self,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        missing_ok: bool = False,
    ) -> object:
        return request_json(
            f"{self.root}/{path}",
            token=self.token,
            payload=payload,
            missing_ok=missing_ok,
        )

    def tag_sha(self, version: str) -> str | None:
        tag = self.api(f"git/ref/tags/v{version}", missing_ok=True)
        if tag is None:
            return None
        # Both lightweight and annotated tags are accepted. Bound nested tag
        # objects so a malformed or cyclic response cannot hold a workflow.
        for _ in range(8):
            target = tag.get("object") if isinstance(tag, dict) else None
            sha = target.get("sha") if isinstance(target, dict) else None
            kind = target.get("type") if isinstance(target, dict) else None
            if not isinstance(sha, str) or COMMIT_SHA.fullmatch(sha) is None:
                break
            if kind == "commit":
                return sha
            if kind != "tag":
                break
            tag = self.api(f"git/tags/{sha}")
        message = "GitHub returned an invalid or excessively nested release tag"
        raise ReleaseError(message)

    def has_release(self, version: str) -> bool:
        # The by-tag endpoint documents published releases only. Listing with
        # authentication includes drafts, so reruns retain an existing draft.
        for page in range(1, MAX_RELEASE_PAGES + 1):
            releases = self.api(f"releases?per_page={RELEASES_PER_PAGE}&page={page}")
            if not isinstance(releases, list) or any(
                not isinstance(release, dict) for release in releases
            ):
                message = "GitHub returned invalid release metadata"
                raise ReleaseError(message)
            if any(release.get("tag_name") == f"v{version}" for release in releases):
                return True
            if len(releases) < RELEASES_PER_PAGE:
                return False
        message = "Too many GitHub releases to establish whether this version exists"
        raise ReleaseError(message)

    def require_tag(self, version: str, sha: str) -> str | None:
        existing = self.tag_sha(version)
        if existing is not None and existing != sha:
            message = (
                f"Tag v{version} belongs to {existing}, not {sha}. "
                "Retry the original release run or bump the version."
            )
            raise ReleaseError(message)
        return existing


def needs_publication(version: str, sha: str, github: GitHub) -> bool:
    files = published_files(version)
    tag_sha = github.tag_sha(version)
    release = github.has_release(version)
    if files.keys() == filenames(version) and tag_sha is not None and release:
        # Documentation-only commits after a completed release are harmless.
        # Nothing is uploaded and the original release tag remains untouched.
        return False
    if tag_sha is not None and tag_sha != sha:
        message = (
            f"Incomplete release v{version} belongs to {tag_sha}, not {sha}. "
            "Retry its original run or bump the version."
        )
        raise ReleaseError(message)
    return True


def validate_artifacts(
    directory: Path,
    version: str,
    *,
    registry: Registry = Registry.PYPI,
) -> None:
    expected = filenames(version, registry)
    if {path.name for path in directory.iterdir()} != expected or any(
        not (directory / name).is_file() or (directory / name).is_symlink()
        for name in expected
    ):
        message = "The artifact must contain exactly this version's wheel and sdist"
        raise ReleaseError(message)


def verify_artifacts(
    directory: Path,
    version: str,
    *,
    registry: Registry = Registry.PYPI,
    complete: bool = False,
) -> None:
    validate_artifacts(directory, version, registry=registry)
    expected = filenames(version, registry)
    remote = published_files(version, registry)
    for name, digest in remote.items():
        with (directory / name).open("rb") as source:
            local = hashlib.file_digest(source, "sha256").hexdigest()
        if local != digest:
            message = (
                f"Published {name} on {registry.label} "
                "differs from the tested artifact. "
                "Use the original run's artifact or release a new version."
            )
            raise ReleaseError(message)
    if complete and remote.keys() != expected:
        message = (
            f"{registry.label} does not yet have both distributions; "
            "retry the upload job"
        )
        raise PublicationPendingError(message)


def wait_for_publication(
    directory: Path, version: str, *, registry: Registry, seconds: int
) -> None:
    """Retry only registry propagation delays, never conflicting published bytes."""
    deadline = monotonic() + seconds
    while True:
        try:
            verify_artifacts(directory, version, registry=registry, complete=True)
        except PublicationPendingError:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise
            sleep(min(PUBLICATION_POLL_INTERVAL, remaining))
            if monotonic() >= deadline:
                raise
        else:
            return


def finalize(directory: Path, version: str, sha: str, github: GitHub) -> None:
    if not github.token:
        message = "GH_TOKEN is required to finish the GitHub release"
        raise ReleaseError(message)
    existing = github.require_tag(version, sha)
    verify_artifacts(directory, version, complete=True)
    if existing is None:
        try:
            github.api("git/refs", payload={"ref": f"refs/tags/v{version}", "sha": sha})
        except RemoteError as error:
            if (
                error.status != HTTPStatus.UNPROCESSABLE_ENTITY
                or github.require_tag(version, sha) is None
            ):
                raise
    if github.require_tag(version, sha) is None:
        message = "The release tag could not be confirmed; retry the original run"
        raise ReleaseError(message)
    if not github.has_release(version):
        try:
            github.api(
                "releases",
                payload={
                    "tag_name": f"v{version}",
                    "target_commitish": sha,
                    "name": f"v{version}",
                    "draft": True,
                    "generate_release_notes": True,
                },
            )
        except RemoteError as error:
            if (
                error.status != HTTPStatus.UNPROCESSABLE_ENTITY
                or not github.has_release(version)
            ):
                raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("guard", "verify", "finalize"))
    parser.add_argument("--project", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--version")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument(
        "--registry", type=Registry, choices=Registry, default=Registry.PYPI
    )
    parser.add_argument("--complete", action="store_true")
    parser.add_argument(
        "--wait",
        type=int,
        metavar="SECONDS",
        help="Wait for complete publication for up to 120 seconds (default: 0)",
    )
    args = parser.parse_args()
    if args.command != "verify" and (
        args.registry is not Registry.PYPI or args.complete
    ):
        parser.error("--registry testpypi and --complete apply only to verify")
    if args.wait is not None:
        if not 0 <= args.wait <= MAX_PUBLICATION_WAIT:
            parser.error("--wait must be between 0 and 120 seconds")
        if args.command != "verify" or not args.complete:
            parser.error("--wait requires verify --complete")
    try:
        version = (
            project_version(args.project)
            if args.command == "guard"
            else validate_version(args.version)
        )
        sha = validate_sha(os.environ.get("GITHUB_SHA", ""))
        github = GitHub(
            os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", "")
        )
        if args.command == "guard":
            publish = needs_publication(version, sha, github)
            print(f"version={version}")
            print(f"publish={str(publish).lower()}")
        elif args.command == "verify":
            github.require_tag(version, sha)
            if args.complete:
                wait_for_publication(
                    args.dist, version, registry=args.registry, seconds=args.wait or 0
                )
            else:
                verify_artifacts(args.dist, version, registry=args.registry)
        else:
            finalize(args.dist, version, sha, github)
    except (ReleaseError, OSError, ValueError, KeyError) as error:
        print(f"Release refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
