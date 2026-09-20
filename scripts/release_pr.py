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
from pathlib import Path
from typing import Any

METADATA = ("pyproject.toml", "uv.lock", "README.md", "CHANGELOG.md")
TITLE = re.compile(
    r"chore\(release\): (0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII
)
SHOT = re.compile(r"docs/shots/[a-z0-9-]+\.(svg|txt)", re.ASCII)
SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
MAX_BUNDLE = 20 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024


def api(endpoint: str, body: dict[str, Any] | None = None) -> Any:
    """Use only GitHub's authenticated API; never interpolate a shell command."""
    command = ["gh", "api", endpoint]
    if body is not None:
        command += [
            "--method",
            "PATCH" if endpoint.endswith("refs/heads/dev") else "POST",
            "--input",
            "-",
        ]
    result = subprocess.run(  # noqa: S603 - fixed executable, validated API paths
        command,
        input=None if body is None else json.dumps(body),
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )
    return json.loads(result.stdout)


def repository() -> str:
    value = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        msg = "Invalid GitHub repository"
        raise ValueError(msg)
    return value


def pull_request(number: int) -> dict[str, Any]:
    repo = repository()
    result: dict[str, Any] = api(f"repos/{repo}/pulls/{number}")
    if (
        number < 1
        or result["state"] != "open"
        or result["head"]["ref"] != "dev"
        or result["base"]["ref"] != "main"
        or result["head"]["repo"]["full_name"] != repo
        or result["base"]["repo"]["full_name"] != repo
    ):
        msg = "Release preparation requires an open dev → main PR in this repository"
        raise ValueError(msg)
    if TITLE.fullmatch(result["title"]) is None:
        msg = "Title must be exactly chore(release): X.Y.Z (e.g. chore(release): 0.2.0)"
        raise ValueError(msg)
    for side in ("head", "base"):
        if SHA.fullmatch(result[side]["sha"]) is None:
            msg = "Invalid PR commit SHA"
            raise ValueError(msg)
    return result


def blob(sha: str) -> bytes:
    if SHA.fullmatch(sha) is None:
        msg = "Invalid blob SHA"
        raise ValueError(msg)
    data = api(f"repos/{repository()}/git/blobs/{sha}")
    if data["encoding"] != "base64" or data["size"] > MAX_FILE:
        msg = "Unsupported release file encoding or size"
        raise ValueError(msg)
    return base64.b64decode(data["content"])


def file_at(path: str, sha: str) -> bytes:
    if path not in METADATA or SHA.fullmatch(sha) is None:
        msg = "Invalid release metadata request"
        raise ValueError(msg)
    data = api(f"repos/{repository()}/contents/{path}?ref={sha}")
    if data["type"] != "file":
        msg = "Release metadata must be regular files"
        raise ValueError(msg)
    return blob(data["sha"])


def version(title: str) -> str:
    match = TITLE.fullmatch(title)
    if match is None:
        msg = "Invalid release title"
        raise ValueError(msg)
    return ".".join(match.groups())


def newer_than_main(pr: dict[str, Any]) -> None:
    before = tomllib.loads(file_at("pyproject.toml", pr["base"]["sha"]).decode())[
        "project"
    ]["version"]
    if not re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", before, flags=re.ASCII
    ):
        msg = "main must have a stable X.Y.Z version"
        raise ValueError(msg)
    wanted = version(pr["title"])
    if tuple(map(int, wanted.split("."))) <= tuple(map(int, before.split("."))):
        msg = f"Release {wanted} must be newer than main's {before}"
        raise ValueError(msg)


def unchanged(pr: dict[str, Any], head: str, title: str) -> None:
    if pr["head"]["sha"] != head or pr["title"] != title:
        msg = "The PR changed during preparation; rerun Prepare release"
        raise ValueError(msg)


def metadata(command: str, root: Path, title: str, *, snapshots: bool = False) -> None:
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


def output(values: dict[str, str]) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                if "\n" in value or "\r" in value:
                    msg = "Multiline workflow output refused"
                    raise ValueError(msg)
                stream.write(f"{key}={value}\n")


def plan(number: int) -> None:
    pr = pull_request(number)
    newer_than_main(pr)
    output(
        {
            "head": pr["head"]["sha"],
            "title": pr["title"],
            "version": version(pr["title"]),
            "number": str(number),
        }
    )
    print(f"Preparing {pr['title']} from {pr['head']['sha']}")


def make_bundle(root: Path, destination: Path) -> None:
    paths = [root / path for path in METADATA]
    paths += sorted((root / "docs" / "shots").glob("*"))
    files: dict[str, str] = {}
    for path in paths:
        name = path.relative_to(root).as_posix()
        if name not in METADATA and SHOT.fullmatch(name) is None:
            msg = f"Unexpected release file: {name}"
            raise ValueError(msg)
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            msg = f"Release files must be regular files inside the checkout: {name}"
            raise ValueError(msg)
        if path.stat().st_size > MAX_FILE:
            msg = f"Release file is too large: {name}"
            raise ValueError(msg)
        files[name] = base64.b64encode(path.read_bytes()).decode("ascii")
    encoded = json.dumps(files)
    if len(encoded.encode()) > MAX_BUNDLE:
        msg = "Release bundle is too large"
        raise ValueError(msg)
    destination.write_text(encoded, encoding="utf-8")


def read_bundle(path: Path) -> dict[str, bytes]:
    if path.stat().st_size > MAX_BUNDLE:
        msg = "Release bundle is too large"
        raise ValueError(msg)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = "Release bundle must contain a file mapping"
        raise TypeError(msg)
    files: dict[str, bytes] = {}
    for name, encoded in data.items():
        if (name not in METADATA and SHOT.fullmatch(name) is None) or not isinstance(
            encoded, str
        ):
            msg = "Release bundle contains an unexpected path or value"
            raise ValueError(msg)
        contents = base64.b64decode(encoded, validate=True)
        if len(contents) > MAX_FILE:
            msg = f"Release file is too large: {name}"
            raise ValueError(msg)
        files[name] = contents
    return files


def reviewed_changes(
    number: int, head: str, title: str, bundle: Path
) -> dict[str, bytes]:
    pr = pull_request(number)
    unchanged(pr, head, title)
    newer_than_main(pr)
    source = api(f"repos/{repository()}/git/commits/{head}")
    tree_sha = source["tree"]["sha"]
    tree = api(f"repos/{repository()}/git/trees/{tree_sha}?recursive=1")
    if tree.get("truncated"):
        msg = "Cannot validate a truncated repository tree"
        raise ValueError(msg)
    allowed = {
        item["path"]: item["sha"]
        for item in tree["tree"]
        if item["type"] == "blob"
        and item["mode"] == "100644"
        and (item["path"] in METADATA or SHOT.fullmatch(item["path"]))
    }
    files = read_bundle(bundle)
    if set(files) != set(allowed) or not set(METADATA) <= set(allowed):
        msg = "Prepared files must match the tracked metadata and screenshots exactly"
        raise ValueError(msg)
    with tempfile.TemporaryDirectory(prefix="flexi-release-review-") as temporary:
        root = Path(temporary)
        for name in METADATA:
            (root / name).write_bytes(blob(allowed[name]))
        metadata("prepare", root, title)
        for name in METADATA:
            if (root / name).read_bytes() != files[name]:
                msg = f"Preparation changed more than the release version in {name}"
                raise ValueError(msg)
        for name, data in files.items():
            if name in METADATA:
                continue
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        metadata("check", root, title, snapshots=True)
    return {
        name: data
        for name, data in files.items()
        if git_blob_sha(data) != allowed[name]
    }


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def commit(number: int, head: str, title: str, bundle: Path, *, write: bool) -> None:
    files = reviewed_changes(number, head, title, bundle)
    output({"changed": str(bool(files)).lower()})
    if not files:
        print("Release metadata and screenshots already match; no commit needed")
        return
    if not write:
        print(f"Validated {len(files)} prepared files")
        return
    repo = repository()
    entries = []
    for path, contents in files.items():
        created = api(
            f"repos/{repo}/git/blobs",
            {
                "content": base64.b64encode(contents).decode("ascii"),
                "encoding": "base64",
            },
        )
        entries.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": created["sha"]}
        )
    source = api(f"repos/{repo}/git/commits/{head}")
    tree = api(
        f"repos/{repo}/git/trees",
        {"base_tree": source["tree"]["sha"], "tree": entries},
    )
    created = api(
        f"repos/{repo}/git/commits",
        {"message": title, "tree": tree["sha"], "parents": [head]},
    )
    unchanged(pull_request(number), head, title)
    # No force: a concurrent push makes this update fail instead of replacing it.
    api(f"repos/{repo}/git/refs/heads/dev", {"sha": created["sha"], "force": False})
    print(f"Prepared {version(title)} in commit {created['sha']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "check", "review", "commit"):
        command = sub.add_parser(name)
        command.add_argument("--pr", type=int, required=True)
        if name != "plan":
            command.add_argument("--head", required=True)
            command.add_argument("--title", required=True)
        if name in {"review", "commit"}:
            command.add_argument("--bundle", type=Path, required=True)
        if name == "check":
            command.add_argument("--root", type=Path, required=True)
    bundle = sub.add_parser("bundle")
    bundle.add_argument("--root", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan(args.pr)
    elif args.command == "bundle":
        make_bundle(args.root, args.output)
    elif args.command == "check":
        pr = pull_request(args.pr)
        unchanged(pr, args.head, args.title)
        newer_than_main(pr)
        metadata("check", args.root, args.title, snapshots=True)
    else:
        commit(
            args.pr, args.head, args.title, args.bundle, write=args.command == "commit"
        )


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(f"Release preparation failed: {error}", file=sys.stderr)
        sys.exit(1)
