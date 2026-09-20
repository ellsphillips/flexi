"""Prepare release PRs without executing candidate code with write credentials."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

METADATA = ("pyproject.toml", "uv.lock", "README.md", "CHANGELOG.md")
STABLE_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SHOT = re.compile(r"docs/shots/[a-z0-9-]+\.(svg|txt)", re.ASCII)
SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
MAX_BUNDLE = 20 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024


class ReleaseError(ValueError):
    """A release input or remote response cannot be used safely."""


def fields(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        message = f"Expected an object for {context}"
        raise ReleaseError(message)
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            message = f"Expected string keys in {context}"
            raise ReleaseError(message)
        result[key] = item
    return result


def text(value: object, context: str) -> str:
    if not isinstance(value, str):
        message = f"Expected a string for {context}"
        raise ReleaseError(message)
    return value


def sha(value: object) -> str:
    result = text(value, "Git SHA")
    if SHA.fullmatch(result) is None:
        message = "Invalid Git SHA"
        raise ReleaseError(message)
    return result


def release_path(path: str) -> bool:
    return path in METADATA or SHOT.fullmatch(path) is not None


@dataclass(frozen=True)
class Version:
    value: str

    def __post_init__(self) -> None:
        if STABLE_VERSION.fullmatch(self.value) is None:
            message = "Version must be stable X.Y.Z without prefixes or leading zeros"
            raise ReleaseError(message)

    @classmethod
    def parse(cls, value: object) -> Version:
        return cls(text(value, "release version"))

    @classmethod
    def from_title(cls, title: str) -> Version:
        prefix = "chore(release): "
        if not title.startswith(prefix):
            message = "Title must be exactly chore(release): X.Y.Z"
            raise ReleaseError(message)
        return cls(title.removeprefix(prefix))

    @property
    def title(self) -> str:
        return f"chore(release): {self.value}"

    @property
    def key(self) -> tuple[tuple[int, str], ...]:
        # Canonical decimal components compare without a machine-integer limit.
        return tuple((len(part), part) for part in self.value.split("."))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ReleaseRequest:
    number: int
    head: str
    version: Version

    def __post_init__(self) -> None:
        if isinstance(self.number, bool) or self.number < 1:
            message = "Pull-request number must be positive"
            raise ReleaseError(message)
        sha(self.head)

    @property
    def title(self) -> str:
        return self.version.title

    def require_match(self, current: ReleaseRequest) -> None:
        if current != self:
            message = "The PR changed during preparation; rerun Prepare release"
            raise ReleaseError(message)


@dataclass(frozen=True)
class PullRequestSnapshot:
    request: ReleaseRequest
    base: str


@dataclass(frozen=True)
class TrackedFile:
    path: str
    sha: str


@dataclass(frozen=True)
class GitTree:
    sha: str
    files: tuple[TrackedFile, ...]


@dataclass(frozen=True)
class FileChange:
    path: str
    content: bytes

    def __post_init__(self) -> None:
        if not release_path(self.path):
            message = f"Unexpected release file path: {self.path}"
            raise ReleaseError(message)
        if len(self.content) > MAX_FILE:
            message = f"Release file is too large: {self.path}"
            raise ReleaseError(message)

    @property
    def sha(self) -> str:
        header = f"blob {len(self.content)}\0".encode()
        return hashlib.sha1(header + self.content, usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class ReleaseBundle:
    files: tuple[FileChange, ...]

    def __post_init__(self) -> None:
        paths = {file.path for file in self.files}
        if len(paths) != len(self.files) or not set(METADATA) <= paths:
            message = "Release bundle must contain unique paths and all metadata files"
            raise ReleaseError(message)

    @classmethod
    def from_checkout(cls, root: Path) -> ReleaseBundle:
        paths = [root / path for path in METADATA]
        paths += sorted((root / "docs" / "shots").glob("*"))
        files = []
        for path in paths:
            name = path.relative_to(root).as_posix()
            if (
                path.is_symlink()
                or not path.is_file()
                or not path.resolve().is_relative_to(root.resolve())
            ):
                message = (
                    f"Release files must be regular files inside the checkout: {name}"
                )
                raise ReleaseError(message)
            if path.stat().st_size > MAX_FILE:
                message = f"Release file is too large: {name}"
                raise ReleaseError(message)
            files.append(FileChange(name, path.read_bytes()))
        return cls(tuple(files))

    @classmethod
    def read(cls, path: Path) -> ReleaseBundle:
        with path.open("rb") as stream:
            encoded = stream.read(MAX_BUNDLE + 1)
        if len(encoded) > MAX_BUNDLE:
            message = "Release bundle is too large"
            raise ReleaseError(message)
        document = fields(json.loads(encoded), "release bundle")
        return cls(
            tuple(
                FileChange(name, base64.b64decode(text(value, name), validate=True))
                for name, value in document.items()
            )
        )

    def write(self, path: Path) -> None:
        encoded = json.dumps(
            {
                file.path: base64.b64encode(file.content).decode("ascii")
                for file in self.files
            }
        ).encode()
        if len(encoded) > MAX_BUNDLE:
            message = "Release bundle is too large"
            raise ReleaseError(message)
        path.write_bytes(encoded)


@dataclass(frozen=True)
class ReviewedRelease:
    request: ReleaseRequest
    base_tree: str
    changes: tuple[FileChange, ...]


class GitHub(Protocol):
    """The repository operations used by release preparation."""

    def pull_request(self, number: int) -> PullRequestSnapshot: ...
    def file_at(self, path: str, ref: str) -> bytes: ...
    def tree_at(self, head: str) -> GitTree: ...
    def blob(self, ref: str) -> bytes: ...
    def create_blob(self, content: bytes) -> str: ...
    def create_tree(self, base_tree: str, entries: tuple[TrackedFile, ...]) -> str: ...
    def create_commit(self, parent: str, tree: str, message: str) -> str: ...
    def advance_dev(self, head: str) -> None: ...


class GitHubClient:
    """Validate GitHub responses before they enter the release workflow."""

    def __init__(self, repository: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            message = "Invalid GitHub repository"
            raise ReleaseError(message)
        self.repository = repository

    def request(
        self,
        method: Literal["GET", "POST", "PATCH"],
        endpoint: str,
        body: dict[str, object] | None = None,
    ) -> object:
        command = [
            "gh",
            "api",
            f"repos/{self.repository}/{endpoint}",
            "--method",
            method,
        ]
        if body is not None:
            command += ["--input", "-"]
        result = subprocess.run(  # noqa: S603 - fixed executable, validated API paths
            command,
            input=None if body is None else json.dumps(body),
            capture_output=True,
            text=True,
            check=True,
            timeout=45,
        )
        return json.loads(result.stdout)

    def pull_request(self, number: int) -> PullRequestSnapshot:
        if isinstance(number, bool) or number < 1:
            message = "Pull-request number must be positive"
            raise ReleaseError(message)
        pr = fields(self.request("GET", f"pulls/{number}"), "pull request")
        head, base = (fields(pr.get(side), side) for side in ("head", "base"))
        head_repo = fields(head.get("repo"), "head repository")
        base_repo = fields(base.get("repo"), "base repository")
        if (
            pr.get("state") != "open"
            or head.get("ref") != "dev"
            or base.get("ref") != "main"
            or head_repo.get("full_name") != self.repository
            or base_repo.get("full_name") != self.repository
        ):
            message = (
                "Release preparation requires an open dev → main PR in this repository"
            )
            raise ReleaseError(message)
        request = ReleaseRequest(
            number,
            sha(head.get("sha")),
            Version.from_title(text(pr.get("title"), "PR title")),
        )
        return PullRequestSnapshot(request, sha(base.get("sha")))

    def blob(self, ref: str) -> bytes:
        data = fields(self.request("GET", f"git/blobs/{sha(ref)}"), "Git blob")
        size = data.get("size")
        if (
            data.get("encoding") != "base64"
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= MAX_FILE
        ):
            message = "Unsupported release file encoding or size"
            raise ReleaseError(message)
        # GitHub wraps base64 with newlines. Reject every other non-base64 byte.
        encoded = text(data.get("content"), "Git blob content").replace("\n", "")
        decoded = base64.b64decode(encoded, validate=True)
        if len(decoded) != size:
            message = "Git blob size does not match its content"
            raise ReleaseError(message)
        return decoded

    def file_at(self, path: str, ref: str) -> bytes:
        if path not in METADATA:
            message = "Invalid release metadata request"
            raise ReleaseError(message)
        data = fields(
            self.request("GET", f"contents/{path}?ref={sha(ref)}"), "release metadata"
        )
        if data.get("type") != "file":
            message = "Release metadata must be regular files"
            raise ReleaseError(message)
        return self.blob(sha(data.get("sha")))

    def tree_at(self, head: str) -> GitTree:
        commit_data = fields(self.request("GET", f"git/commits/{sha(head)}"), "commit")
        tree_sha = sha(fields(commit_data.get("tree"), "commit tree").get("sha"))
        data = fields(
            self.request("GET", f"git/trees/{tree_sha}?recursive=1"), "Git tree"
        )
        if data.get("truncated") is not False:
            message = "Cannot validate a truncated or incomplete repository tree"
            raise ReleaseError(message)
        entries = data.get("tree")
        if not isinstance(entries, list):
            message = "Git tree must contain an entries list"
            raise ReleaseError(message)
        files = []
        seen = set()
        for entry in entries:
            item = fields(entry, "Git tree entry")
            path = text(item.get("path"), "Git tree path")
            if path in seen:
                message = "Git tree contains duplicate paths"
                raise ReleaseError(message)
            seen.add(path)
            if not release_path(path):
                continue
            if item.get("type") != "blob" or item.get("mode") != "100644":
                message = f"Release files must be tracked regular files: {path}"
                raise ReleaseError(message)
            files.append(TrackedFile(path, sha(item.get("sha"))))
        return GitTree(tree_sha, tuple(files))

    def create_blob(self, content: bytes) -> str:
        data = fields(
            self.request(
                "POST",
                "git/blobs",
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                },
            ),
            "created blob",
        )
        return sha(data.get("sha"))

    def create_tree(self, base_tree: str, entries: tuple[TrackedFile, ...]) -> str:
        data = fields(
            self.request(
                "POST",
                "git/trees",
                {
                    "base_tree": sha(base_tree),
                    "tree": [
                        {
                            "path": entry.path,
                            "mode": "100644",
                            "type": "blob",
                            "sha": sha(entry.sha),
                        }
                        for entry in entries
                    ],
                },
            ),
            "created tree",
        )
        return sha(data.get("sha"))

    def create_commit(self, parent: str, tree: str, message: str) -> str:
        data = fields(
            self.request(
                "POST",
                "git/commits",
                {"message": message, "tree": sha(tree), "parents": [sha(parent)]},
            ),
            "created commit",
        )
        return sha(data.get("sha"))

    def advance_dev(self, head: str) -> None:
        self.request("PATCH", "git/refs/heads/dev", {"sha": sha(head), "force": False})


def metadata(
    command: Literal["prepare", "check"],
    root: Path,
    title: str,
    *,
    snapshots: bool = False,
) -> None:
    args = [
        sys.executable,
        "-I",
        str(Path(__file__).with_name("prepare_release.py")),
        command,
        "--root",
        str(root),
        "--title",
        title,
    ]
    if snapshots:
        args.append("--check-snapshots")
    subprocess.run(args, check=True, timeout=30)  # noqa: S603 - trusted sibling script


def plan(client: GitHub, number: int) -> ReleaseRequest:
    snapshot = client.pull_request(number)
    project = fields(
        tomllib.loads(client.file_at("pyproject.toml", snapshot.base).decode()),
        "pyproject.toml",
    )
    before = Version.parse(fields(project.get("project"), "project").get("version"))
    if snapshot.request.version.key <= before.key:
        message = (
            f"Release {snapshot.request.version} must be newer than main's {before}"
        )
        raise ReleaseError(message)
    return snapshot.request


def check(client: GitHub, request: ReleaseRequest, root: Path) -> None:
    request.require_match(plan(client, request.number))
    metadata("check", root, request.title, snapshots=True)


def review(
    client: GitHub, request: ReleaseRequest, bundle: ReleaseBundle
) -> ReviewedRelease:
    request.require_match(plan(client, request.number))
    tree = client.tree_at(request.head)
    tracked = {file.path: file.sha for file in tree.files}
    files = {file.path: file.content for file in bundle.files}
    if set(files) != set(tracked) or not set(METADATA) <= set(tracked):
        message = (
            "Prepared files must match the tracked metadata and screenshots exactly"
        )
        raise ReleaseError(message)
    with tempfile.TemporaryDirectory(prefix="flexi-release-review-") as temporary:
        root = Path(temporary)
        for name in METADATA:
            (root / name).write_bytes(client.blob(tracked[name]))
        metadata("prepare", root, request.title)
        for name in METADATA:
            if (root / name).read_bytes() != files[name]:
                message = f"Preparation changed more than the release version in {name}"
                raise ReleaseError(message)
        for file in bundle.files:
            if file.path not in METADATA:
                path = root / file.path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(file.content)
        metadata("check", root, request.title, snapshots=True)
    changes = tuple(file for file in bundle.files if file.sha != tracked[file.path])
    return ReviewedRelease(request, tree.sha, changes)


def commit(client: GitHub, reviewed: ReviewedRelease) -> str | None:
    request = reviewed.request
    request.require_match(plan(client, request.number))
    if not reviewed.changes:
        return None
    entries = tuple(
        TrackedFile(file.path, client.create_blob(file.content))
        for file in reviewed.changes
    )
    tree = client.create_tree(reviewed.base_tree, entries)
    created = client.create_commit(request.head, tree, request.title)
    # Revalidate just before moving dev. A non-forced update also rejects a
    # concurrent push that lands after this last check.
    request.require_match(plan(client, request.number))
    client.advance_dev(created)
    return created


def output(values: Mapping[str, str]) -> None:
    if any("\n" in value or "\r" in value for value in values.values()):
        message = "Multiline workflow output refused"
        raise ReleaseError(message)
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in values.items())


class Arguments(argparse.Namespace):
    command: str
    pr: int
    head: str
    version: Version
    root: Path
    bundle: Path
    output: Path


def arguments(argv: Sequence[str] | None) -> Arguments:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "check", "review", "commit"):
        command = sub.add_parser(name)
        command.add_argument("--pr", type=int, required=True)
        if name != "plan":
            command.add_argument("--head", required=True)
            command.add_argument(
                "--title", dest="version", type=Version.from_title, required=True
            )
        if name in {"review", "commit"}:
            command.add_argument("--bundle", type=Path, required=True)
        if name == "check":
            command.add_argument("--root", type=Path, required=True)
    bundle = sub.add_parser("bundle")
    bundle.add_argument("--root", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv, namespace=Arguments())


def run(args: Arguments) -> None:
    if args.command == "bundle":
        ReleaseBundle.from_checkout(args.root).write(args.output)
        return
    client = GitHubClient(os.environ.get("GITHUB_REPOSITORY", ""))
    if args.command == "plan":
        request = plan(client, args.pr)
        output(
            {
                "head": request.head,
                "title": request.title,
                "version": str(request.version),
                "number": str(request.number),
            }
        )
        print(f"Preparing {request.title} from {request.head}")
        return
    request = ReleaseRequest(args.pr, args.head, args.version)
    if args.command == "check":
        check(client, request, args.root)
        return
    reviewed = review(client, request, ReleaseBundle.read(args.bundle))
    output({"changed": str(bool(reviewed.changes)).lower()})
    if args.command == "commit":
        created = commit(client, reviewed)
        if created is not None:
            print(f"Prepared {request.version} in commit {created}")
            return
    if reviewed.changes:
        print(f"Validated {len(reviewed.changes)} prepared files")
    else:
        print("Release metadata and screenshots already match; no commit needed")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        run(arguments(argv))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Release preparation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
