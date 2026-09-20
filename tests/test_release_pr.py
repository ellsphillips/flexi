"""Release preparation treats generated artifacts as untrusted input."""

from __future__ import annotations

import base64
import copy
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_pr.py"
HEAD, BASE, TREE, CREATED = (letter * 40 for letter in "abcd")
TITLE = "chore(release): 0.2.0"


class Remote:
    """A repository API that records every write and rejects unexpected calls."""

    def __init__(self, module: dict[str, Any], root: Path) -> None:
        self.module, self.root = module, root
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
        self.pr: dict[str, Any] = {
            "state": "open",
            "title": TITLE,
            "head": {"ref": "dev", "sha": HEAD, "repo": {"full_name": "owner/flexi"}},
            "base": {"ref": "main", "sha": BASE, "repo": {"full_name": "owner/flexi"}},
        }
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.move_on_commit = False

    def request(
        self, endpoint: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        path = endpoint.removeprefix("repos/owner/flexi/")
        if body is not None:
            self.writes.append((path, body))
            if path == "git/commits" and self.move_on_commit:
                self.pr["head"]["sha"] = "e" * 40
            return {"sha": CREATED}
        if path.startswith("pulls/"):
            return copy.deepcopy(self.pr)
        if path == f"contents/pyproject.toml?ref={BASE}":
            return {"type": "file", "sha": self.module["git_blob_sha"](self.main)}
        if path == f"git/commits/{HEAD}":
            return {"tree": {"sha": TREE}}
        if path == f"git/trees/{TREE}?recursive=1":
            return {
                "tree": [
                    {
                        "path": name,
                        "type": "blob",
                        "mode": "100644",
                        "sha": self.module["git_blob_sha"](data),
                    }
                    for name, data in self.files.items()
                ]
            }
        for data in (*self.files.values(), self.main):
            if path == f"git/blobs/{self.module['git_blob_sha'](data)}":
                return {
                    "encoding": "base64",
                    "size": len(data),
                    "content": base64.b64encode(data).decode(),
                }
        raise AssertionError(endpoint)


@pytest.fixture
def release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], Remote, Path]:
    loaded = runpy.run_path(str(SCRIPT))
    module: dict[str, Any] = loaded["commit"].__globals__
    remote = Remote(module, tmp_path / "checkout")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/flexi")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setitem(module, "api", remote.request)
    for name, data in remote.files.items():
        path = remote.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    module["metadata"]("prepare", remote.root, TITLE)
    for path in (remote.root / "docs" / "shots").iterdir():
        path.write_bytes(path.read_bytes().replace(b"v0.1.0", b"v0.2.0"))
    bundle = tmp_path / "bundle.json"
    module["make_bundle"](remote.root, bundle)
    return module, remote, bundle


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "closed"),
        ("head.ref", "feature"),
        ("base.ref", "dev"),
        ("head.repo.full_name", "fork/flexi"),
        ("base.repo.full_name", "other/flexi"),
        ("head.sha", "not-a-sha"),
        ("base.sha", "not-a-sha"),
        ("title", "chore(release): v0.2.0"),
        ("title", "chore(release): 00.2.0"),
        ("title", "chore(release): 0.2.0rc1"),
        ("title", "chore(release): 0.2.0\n"),
    ],
)
def test_plan_refuses_ineligible_pull_requests(
    release: tuple[dict[str, Any], Remote, Path], field: str, value: str
) -> None:
    module, remote, _bundle = release
    target = remote.pr
    *parents, key = field.split(".")
    for parent in parents:
        target = target[parent]
    target[key] = value
    with pytest.raises(ValueError, match=r"requires an open|Title must|Invalid PR"):
        module["plan"](12)
    assert remote.writes == []


@pytest.mark.parametrize("version", ["0.1.0", "0.0.9"])
def test_plan_requires_a_version_newer_than_main(
    release: tuple[dict[str, Any], Remote, Path], version: str
) -> None:
    module, remote, _bundle = release
    remote.pr["title"] = f"chore(release): {version}"
    with pytest.raises(ValueError, match="newer than main"):
        module["plan"](12)
    assert remote.writes == []


@pytest.mark.parametrize("changed", ["head", "title"])
def test_stale_preparation_writes_nothing(
    release: tuple[dict[str, Any], Remote, Path], changed: str
) -> None:
    module, remote, bundle = release
    if changed == "head":
        remote.pr["head"]["sha"] = "f" * 40
    else:
        remote.pr["title"] = "chore(release): 0.3.0"
    with pytest.raises(ValueError, match="PR changed"):
        module["commit"](12, HEAD, TITLE, bundle, write=True)
    assert remote.writes == []


@pytest.mark.parametrize(
    "path", ["../README.md", "docs/shots/../secret.txt", "src/flexi/app.py"]
)
def test_bundle_cannot_name_other_files(
    release: tuple[dict[str, Any], Remote, Path], path: str
) -> None:
    module, _remote, bundle = release
    bundle.write_text(json.dumps({path: "YQ=="}), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected path"):
        module["read_bundle"](bundle)


def test_bundle_rejects_symlinks(
    release: tuple[dict[str, Any], Remote, Path], tmp_path: Path
) -> None:
    module, remote, bundle = release
    outside = tmp_path / "outside.md"
    (remote.root / "README.md").rename(outside)
    try:
        (remote.root / "README.md").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    with pytest.raises(ValueError, match="regular files inside"):
        module["make_bundle"](remote.root, bundle)


@pytest.mark.parametrize("limit", ["MAX_BUNDLE", "MAX_FILE"])
def test_bundle_size_limits_are_enforced(
    release: tuple[dict[str, Any], Remote, Path],
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    module, _remote, bundle = release
    monkeypatch.setitem(module, limit, 8)
    with pytest.raises(ValueError, match="too large"):
        module["read_bundle"](bundle)


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("pyproject.toml", b"httpx>=0.27", b"unexpected-package>=1"),
        ("uv.lock", b'"0.27.0"', b'"99.0.0"'),
    ],
)
def test_generated_metadata_cannot_change_dependencies(
    release: tuple[dict[str, Any], Remote, Path], path: str, before: bytes, after: bytes
) -> None:
    module, remote, bundle = release
    target = remote.root / path
    target.write_bytes(target.read_bytes().replace(before, after))
    module["make_bundle"](remote.root, bundle)
    with pytest.raises(ValueError, match="Preparation changed more than"):
        module["commit"](12, HEAD, TITLE, bundle, write=True)
    assert remote.writes == []


@pytest.mark.parametrize("already_prepared", [False, True])
def test_read_only_review_and_no_op_commit_never_write(
    release: tuple[dict[str, Any], Remote, Path], already_prepared: bool
) -> None:
    module, remote, bundle = release
    if already_prepared:
        remote.files = module["read_bundle"](bundle)
    module["commit"](12, HEAD, TITLE, bundle, write=already_prepared)
    assert remote.writes == []


def test_commit_extends_reviewed_head_without_force(
    release: tuple[dict[str, Any], Remote, Path],
) -> None:
    module, remote, bundle = release
    module["commit"](12, HEAD, TITLE, bundle, write=True)
    writes = dict(remote.writes)
    assert writes["git/trees"]["base_tree"] == TREE
    assert writes["git/commits"]["parents"] == [HEAD]
    assert writes["git/commits"]["message"] == TITLE
    assert remote.writes[-1] == ("git/refs/heads/dev", {"sha": CREATED, "force": False})


def test_concurrent_change_prevents_final_ref_update(
    release: tuple[dict[str, Any], Remote, Path],
) -> None:
    module, remote, bundle = release
    remote.move_on_commit = True
    with pytest.raises(ValueError, match="PR changed"):
        module["commit"](12, HEAD, TITLE, bundle, write=True)
    assert "git/commits" in dict(remote.writes)
    assert "git/refs/heads/dev" not in dict(remote.writes)
