"""Release preparation treats generated artifacts as untrusted input."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import pytest
from scripts import release_pr as release

HEAD, BASE, TREE, CREATED = (letter * 40 for letter in "abcd")
TITLE = "chore(release): 0.2.0"


@dataclass(frozen=True)
class BlobWrite:
    content: bytes


@dataclass(frozen=True)
class TreeWrite:
    base: str
    entries: tuple[release.TrackedFile, ...]


@dataclass(frozen=True)
class CommitWrite:
    parent: str
    tree: str
    message: str


@dataclass(frozen=True)
class RefWrite:
    head: str


class Remote:
    """A typed GitHub boundary that records writes and rejects unexpected reads."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files = {
            "pyproject.toml": (
                b'[project]\nname = "flexi"\nversion = "0.1.0"\n'
                b'dependencies = ["httpx>=0.27"]\n'
            ),
            "uv.lock": (
                b'version = 1\n[[package]]\nname = "flexi"\nversion = "0.1.0"\n'
                b'source = { editable = "." }\n[[package]]\n'
                b'name = "httpx"\nversion = "0.27.0"\n'
            ),
            "README.md": b"![version](https://example.com/badge/version-0.1.0-00AAAD.svg)\n",
            "CHANGELOG.md": b"# Changes\n\n## Unreleased\n\n- A useful improvement.\n",
            "docs/shots/dashboard.txt": b"  flexi  v0.1.0\n",
            "docs/shots/dashboard.svg": b"<svg>v0.1.0</svg>\n",
        }
        self.main = self.files["pyproject.toml"]
        self.snapshot = release.PullRequestSnapshot(
            release.ReleaseRequest(12, HEAD, release.Version.from_title(TITLE)), BASE
        )
        self.writes: list[BlobWrite | TreeWrite | CommitWrite | RefWrite] = []
        self.move_on_commit: Literal["head", "title"] | None = None

    def pull_request(self, number: int) -> release.PullRequestSnapshot:
        assert number == 12
        return self.snapshot

    def file_at(self, path: str, ref: str) -> bytes:
        assert (path, ref) == ("pyproject.toml", BASE)
        return self.main

    def tree_at(self, head: str) -> release.GitTree:
        assert head == HEAD
        return release.GitTree(
            TREE,
            tuple(
                release.TrackedFile(name, release.FileChange(name, data).sha)
                for name, data in self.files.items()
            ),
        )

    def blob(self, sha: str) -> bytes:
        for name, data in self.files.items():
            if release.FileChange(name, data).sha == sha:
                return data
        raise AssertionError(sha)

    def create_blob(self, content: bytes) -> str:
        self.writes.append(BlobWrite(content))
        return release.FileChange("README.md", content).sha

    def create_tree(
        self, base_tree: str, entries: tuple[release.TrackedFile, ...]
    ) -> str:
        self.writes.append(TreeWrite(base_tree, entries))
        return CREATED

    def create_commit(self, parent: str, tree: str, message: str) -> str:
        self.writes.append(CommitWrite(parent, tree, message))
        if self.move_on_commit is not None:
            self.move(self.move_on_commit)
        return CREATED

    def advance_dev(self, head: str) -> None:
        self.writes.append(RefWrite(head))

    def move(self, changed: Literal["head", "title"]) -> None:
        request = self.snapshot.request
        current = (
            replace(request, head="f" * 40)
            if changed == "head"
            else replace(request, version=release.Version.parse("0.3.0"))
        )
        self.snapshot = replace(self.snapshot, request=current)


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[Remote, release.ReleaseBundle]:
    remote = Remote(tmp_path / "checkout")
    for name, data in remote.files.items():
        path = remote.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    release.metadata("prepare", remote.root, TITLE)
    for path in (remote.root / "docs" / "shots").iterdir():
        path.write_bytes(path.read_bytes().replace(b"v0.1.0", b"v0.2.0"))
    return remote, release.ReleaseBundle.from_checkout(remote.root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "closed"),
        ("head.ref", "feature"),
        ("base.ref", "dev"),
        ("head.repo", "fork/flexi"),
        ("base.repo", "other/flexi"),
        ("head.sha", "not-a-sha"),
        ("base.sha", "not-a-sha"),
        ("title", "chore(release): v0.2.0"),
        ("title", "chore(release): 00.2.0"),
        ("title", "chore(release): 0.2.0rc1"),
        ("title", "chore(release): 0.2.0\n"),
    ],
)
def test_github_boundary_refuses_ineligible_pull_requests(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    head_repo: dict[str, object] = {"full_name": "owner/flexi"}
    base_repo: dict[str, object] = {"full_name": "owner/flexi"}
    head: dict[str, object] = {"ref": "dev", "sha": HEAD, "repo": head_repo}
    base: dict[str, object] = {"ref": "main", "sha": BASE, "repo": base_repo}
    response: dict[str, object] = {
        "state": "open",
        "title": TITLE,
        "head": head,
        "base": base,
    }
    fields = {
        "state": (response, "state"),
        "title": (response, "title"),
        "head.ref": (head, "ref"),
        "base.ref": (base, "ref"),
        "head.sha": (head, "sha"),
        "base.sha": (base, "sha"),
        "head.repo": (head_repo, "full_name"),
        "base.repo": (base_repo, "full_name"),
    }
    owner, name = fields[field]
    owner[name] = value

    def request(
        self: release.GitHubClient,
        method: str,
        endpoint: str,
        body: dict[str, object] | None = None,
    ) -> object:
        assert method == "GET"
        assert body is None
        return response

    monkeypatch.setattr(release.GitHubClient, "request", request)
    with pytest.raises(ValueError, match=r"open|Title|title|SHA|sha|version|Version"):
        release.GitHubClient("owner/flexi").pull_request(12)


@pytest.mark.parametrize("version", ["0.1.0", "0.0.9"])
def test_plan_requires_a_version_newer_than_main(
    prepared: tuple[Remote, release.ReleaseBundle], version: str
) -> None:
    remote, _bundle = prepared
    remote.snapshot = replace(
        remote.snapshot,
        request=replace(
            remote.snapshot.request, version=release.Version.parse(version)
        ),
    )
    with pytest.raises(ValueError, match="newer than main"):
        release.plan(remote, 12)
    assert remote.writes == []


def test_plan_returns_the_validated_request_without_writing(
    prepared: tuple[Remote, release.ReleaseBundle],
) -> None:
    remote, _bundle = prepared
    request = release.plan(remote, 12)
    assert request == remote.snapshot.request
    assert str(request.version) == "0.2.0"
    assert request.version.title == TITLE
    assert remote.writes == []


@pytest.mark.parametrize("changed", ["head", "title"])
def test_stale_preparation_writes_nothing(
    prepared: tuple[Remote, release.ReleaseBundle], changed: Literal["head", "title"]
) -> None:
    remote, bundle = prepared
    request = remote.snapshot.request
    remote.move(changed)
    with pytest.raises(ValueError, match="PR changed"):
        release.review(remote, request, bundle)
    assert remote.writes == []


@pytest.mark.parametrize(
    "path", ["../README.md", "docs/shots/../secret.txt", "src/flexi/app.py"]
)
def test_bundle_cannot_name_other_files(tmp_path: Path, path: str) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({path: "YQ=="}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"unexpected path|Unexpected release file"):
        release.ReleaseBundle.read(bundle)


def test_bundle_rejects_symlinks(
    prepared: tuple[Remote, release.ReleaseBundle], tmp_path: Path
) -> None:
    remote, _bundle = prepared
    outside = tmp_path / "outside.md"
    (remote.root / "README.md").rename(outside)
    try:
        (remote.root / "README.md").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    with pytest.raises(ValueError, match="regular files inside"):
        release.ReleaseBundle.from_checkout(remote.root)


@pytest.mark.parametrize("limit", ["MAX_BUNDLE", "MAX_FILE"])
def test_bundle_size_limits_are_enforced(
    prepared: tuple[Remote, release.ReleaseBundle],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    _remote, bundle = prepared
    path = tmp_path / "bundle.json"
    bundle.write(path)
    monkeypatch.setattr(release, limit, 8)
    with pytest.raises(ValueError, match="too large"):
        release.ReleaseBundle.read(path)


def test_bundle_roundtrip_preserves_paths_and_bytes(
    prepared: tuple[Remote, release.ReleaseBundle], tmp_path: Path
) -> None:
    _remote, bundle = prepared
    path = tmp_path / "bundle.json"
    bundle.write(path)
    assert release.ReleaseBundle.read(path) == bundle


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("pyproject.toml", b"httpx>=0.27", b"unexpected-package>=1"),
        ("uv.lock", b'"0.27.0"', b'"99.0.0"'),
    ],
)
def test_generated_metadata_cannot_change_dependencies(
    prepared: tuple[Remote, release.ReleaseBundle],
    path: str,
    before: bytes,
    after: bytes,
) -> None:
    remote, bundle = prepared
    tampered = release.ReleaseBundle(
        tuple(
            release.FileChange(file.path, file.content.replace(before, after))
            if file.path == path
            else file
            for file in bundle.files
        )
    )
    with pytest.raises(ValueError, match="Preparation changed more than"):
        release.review(remote, remote.snapshot.request, tampered)
    assert remote.writes == []


@pytest.mark.parametrize("already_prepared", [False, True])
def test_read_only_review_and_no_op_commit_never_write(
    prepared: tuple[Remote, release.ReleaseBundle], already_prepared: bool
) -> None:
    remote, bundle = prepared
    if already_prepared:
        remote.files = {file.path: file.content for file in bundle.files}
    reviewed = release.review(remote, remote.snapshot.request, bundle)
    assert bool(reviewed.changes) is not already_prepared
    if already_prepared:
        assert release.commit(remote, reviewed) is None
    assert remote.writes == []


def test_commit_extends_reviewed_head_and_preserves_original_tree(
    prepared: tuple[Remote, release.ReleaseBundle],
) -> None:
    remote, bundle = prepared
    reviewed = release.review(remote, remote.snapshot.request, bundle)
    assert reviewed.request == remote.snapshot.request
    assert reviewed.base_tree == TREE
    assert release.commit(remote, reviewed) == CREATED
    assert remote.writes == [
        *(BlobWrite(file.content) for file in reviewed.changes),
        TreeWrite(
            TREE,
            tuple(
                release.TrackedFile(file.path, file.sha) for file in reviewed.changes
            ),
        ),
        CommitWrite(HEAD, CREATED, TITLE),
        RefWrite(CREATED),
    ]


@pytest.mark.parametrize("changed", ["head", "title"])
def test_stale_review_cannot_create_remote_objects(
    prepared: tuple[Remote, release.ReleaseBundle], changed: Literal["head", "title"]
) -> None:
    remote, bundle = prepared
    reviewed = release.review(remote, remote.snapshot.request, bundle)
    remote.move(changed)
    with pytest.raises(ValueError, match="PR changed"):
        release.commit(remote, reviewed)
    assert remote.writes == []


@pytest.mark.parametrize("changed", ["head", "title"])
def test_concurrent_change_prevents_final_ref_update(
    prepared: tuple[Remote, release.ReleaseBundle], changed: Literal["head", "title"]
) -> None:
    remote, bundle = prepared
    reviewed = release.review(remote, remote.snapshot.request, bundle)
    remote.move_on_commit = changed
    with pytest.raises(ValueError, match="PR changed"):
        release.commit(remote, reviewed)
    assert any(isinstance(write, CommitWrite) for write in remote.writes)
    assert not any(isinstance(write, RefWrite) for write in remote.writes)


def test_check_validates_the_current_prepared_checkout_without_writing(
    prepared: tuple[Remote, release.ReleaseBundle],
) -> None:
    remote, _bundle = prepared
    release.check(remote, remote.snapshot.request, remote.root)
    assert remote.writes == []


def test_github_ref_update_is_explicitly_non_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def request(
        self: release.GitHubClient,
        method: str,
        endpoint: str,
        body: dict[str, object] | None = None,
    ) -> object:
        calls.append((method, endpoint, body))
        return {"object": {"sha": CREATED}}

    monkeypatch.setattr(release.GitHubClient, "request", request)
    release.GitHubClient("owner/flexi").advance_dev(CREATED)
    assert calls == [("PATCH", "git/refs/heads/dev", {"sha": CREATED, "force": False})]


@pytest.fixture
def github(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[release.GitHubClient, dict[str, object]]:
    responses: dict[str, object] = {}
    client = release.GitHubClient("owner/flexi")

    def request(
        method: Literal["GET", "POST", "PATCH"],
        endpoint: str,
        body: dict[str, object] | None = None,
    ) -> object:
        assert method == "GET"
        assert body is None
        return responses[endpoint]

    monkeypatch.setattr(client, "request", request)
    return client, responses


@pytest.mark.parametrize("response", [None, True, "{}", [], {}, {"head": None}])
def test_github_boundary_rejects_malformed_pull_request_objects(
    github: tuple[release.GitHubClient, dict[str, object]], response: object
) -> None:
    client, responses = github
    responses["pulls/12"] = response
    with pytest.raises(release.ReleaseError, match="Expected an object"):
        client.pull_request(12)


@pytest.mark.parametrize("number", [True, False, 0, -1])
def test_github_boundary_rejects_invalid_numbers_before_requesting(
    github: tuple[release.GitHubClient, dict[str, object]], number: int
) -> None:
    client, _responses = github
    with pytest.raises(release.ReleaseError, match="must be positive"):
        client.pull_request(number)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("encoding", "utf8"),
        ("encoding", None),
        ("size", None),
        ("size", True),
        ("size", -1),
        ("size", release.MAX_FILE + 1),
        ("content", None),
        ("content", True),
    ],
)
def test_github_blob_rejects_malformed_encoding_size_and_content(
    github: tuple[release.GitHubClient, dict[str, object]],
    field: str,
    value: object,
) -> None:
    client, responses = github
    blob: dict[str, object] = {"encoding": "base64", "size": 1, "content": "YQ=="}
    blob[field] = value
    responses[f"git/blobs/{HEAD}"] = blob
    with pytest.raises(
        release.ReleaseError, match=r"encoding or size|Expected a string"
    ):
        client.blob(HEAD)


def test_github_blob_validates_decoded_size_instead_of_trusting_metadata(
    github: tuple[release.GitHubClient, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, responses = github
    monkeypatch.setattr(release, "MAX_FILE", 1)
    responses[f"git/blobs/{HEAD}"] = {
        "encoding": "base64",
        "size": 1,
        "content": base64.b64encode(b"larger than declared").decode("ascii"),
    }
    with pytest.raises(release.ReleaseError, match="size does not match"):
        client.blob(HEAD)


def test_github_blob_accepts_github_line_wrapping(
    github: tuple[release.GitHubClient, dict[str, object]],
) -> None:
    client, responses = github
    responses[f"git/blobs/{HEAD}"] = {
        "encoding": "base64",
        "size": 1,
        "content": "Y\nQ==\n",
    }
    assert client.blob(HEAD) == b"a"


@pytest.mark.parametrize("truncated", [None, True, 0, 1, "false"])
def test_github_tree_requires_an_explicit_complete_result(
    github: tuple[release.GitHubClient, dict[str, object]], truncated: object
) -> None:
    client, responses = github
    responses[f"git/commits/{HEAD}"] = {"tree": {"sha": TREE}}
    responses[f"git/trees/{TREE}?recursive=1"] = {"truncated": truncated, "tree": []}
    with pytest.raises(release.ReleaseError, match="truncated or incomplete"):
        client.tree_at(HEAD)


def test_github_tree_rejects_duplicate_paths(
    github: tuple[release.GitHubClient, dict[str, object]],
) -> None:
    client, responses = github
    entry = {"path": "README.md", "type": "blob", "mode": "100644", "sha": HEAD}
    responses[f"git/commits/{HEAD}"] = {"tree": {"sha": TREE}}
    responses[f"git/trees/{TREE}?recursive=1"] = {
        "truncated": False,
        "tree": [entry, entry],
    }
    with pytest.raises(release.ReleaseError, match="duplicate paths"):
        client.tree_at(HEAD)


@pytest.mark.parametrize(
    "entry",
    [
        None,
        True,
        "README.md",
        {},
        {"path": "README.md", "type": "blob", "mode": "120000", "sha": HEAD},
        {"path": "README.md", "type": "blob", "mode": "100644", "sha": "bad"},
    ],
)
def test_github_tree_rejects_malformed_or_unsafe_release_entries(
    github: tuple[release.GitHubClient, dict[str, object]], entry: object
) -> None:
    client, responses = github
    responses[f"git/commits/{HEAD}"] = {"tree": {"sha": TREE}}
    responses[f"git/trees/{TREE}?recursive=1"] = {"truncated": False, "tree": [entry]}
    with pytest.raises(release.ReleaseError, match=r"Expected|regular files|SHA"):
        client.tree_at(HEAD)
