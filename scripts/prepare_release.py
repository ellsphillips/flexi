"""Prepare or verify release metadata without invoking Git or dependency tools."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
import tomllib
from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
TITLE = re.compile(rf"chore\(release\): (?P<version>{VERSION})")
ASSIGNMENT = re.compile(
    r"(?m)^[ \t]*version[ \t]*=[ \t]*(?P<quote>['\"])"
    r"(?P<version>[^'\"\r\n]*)(?P=quote)[ \t]*(?:#[^\r\n]*)?\r?$"
)
BADGE = re.compile(
    r"/badge/version-(?P<version>[^/\s?#]+)-[A-Za-z0-9]+(?:\.svg)?(?=[/?#)\s]|$)"
)
FILES = ("pyproject.toml", "uv.lock", "README.md", "CHANGELOG.md")


class ReleaseError(ValueError):
    """A release input that needs correcting before metadata can be changed."""


@dataclass(frozen=True)
class Document:
    path: Path
    text: str
    mode: int


def version(value: object) -> str:
    """Accept canonical stable SemVer, including numbers larger than machine ints."""
    if not isinstance(value, str) or re.fullmatch(VERSION, value) is None:
        message = (
            "Version must be stable SemVer X.Y.Z without prefixes or leading zeros"
        )
        raise ReleaseError(message)
    return value


def version_key(value: str) -> tuple[tuple[int, str], ...]:
    return tuple((len(part), part) for part in value.split("."))


def release_version(title: str) -> str:
    found = TITLE.fullmatch(title)
    if found is None:
        message = "Title must be exactly 'chore(release): X.Y.Z' with stable SemVer"
        raise ReleaseError(message)
    return found["version"]


def read_document(root: Path, relative: str) -> Document:
    """Read a regular file inside the checkout without accepting symlink paths."""
    path = root / relative
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            message = f"Symlinks are not allowed: {relative}"
            raise ReleaseError(message)
    details = path.stat()
    if not stat.S_ISREG(details.st_mode) or not path.resolve().is_relative_to(root):
        message = f"Expected a regular file inside the checkout: {relative}"
        raise ReleaseError(message)
    return Document(
        path, path.read_bytes().decode("utf-8"), stat.S_IMODE(details.st_mode)
    )


def replace_version(text: str, expected: str, target: str, name: str) -> str:
    matches = tuple(ASSIGNMENT.finditer(text))
    if len(matches) != 1 or matches[0]["version"] != expected:
        message = f"{name} must contain one simple quoted version assignment"
        raise ReleaseError(message)
    start, end = matches[0].span("version")
    return text[:start] + target + text[end:]


def project_metadata(text: str, target: str) -> tuple[str, str]:
    parsed = tomllib.loads(text)
    project = parsed.get("project")
    if not isinstance(project, dict) or project.get("name") != "flexi":
        message = "pyproject.toml must declare [project] name = 'flexi'"
        raise ReleaseError(message)
    current = version(project.get("version"))
    headers = tuple(re.finditer(r"(?m)^[ \t]*\[.*\][ \t]*(?:#[^\r\n]*)?\r?$", text))
    sections = [
        (
            header.end(),
            headers[index + 1].start() if index + 1 < len(headers) else len(text),
        )
        for index, header in enumerate(headers)
        if re.fullmatch(r"[ \t]*\[project\][ \t]*(?:#[^\r\n]*)?\r?", header[0])
    ]
    if len(sections) != 1:
        message = "pyproject.toml must contain one explicit [project] table"
        raise ReleaseError(message)
    start, end = sections[0]
    updated = replace_version(text[start:end], current, target, "[project]")
    return current, text[:start] + updated + text[end:]


def lock_metadata(text: str, target: str) -> tuple[str, str]:
    tomllib.loads(text)
    headers = tuple(re.finditer(r"(?m)^\[\[package\]\][ \t]*(?:#[^\r\n]*)?\r?$", text))
    found: list[tuple[int, int, str]] = []
    for index, header in enumerate(headers):
        start = header.start()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        package = tomllib.loads(text[start:end])["package"][0]
        if package.get("name") == "flexi" and package.get("source") == {
            "editable": "."
        }:
            found.append((start, end, version(package.get("version"))))
    if len(found) != 1:
        message = (
            "uv.lock must contain exactly one local flexi package with editable = '.'"
        )
        raise ReleaseError(message)
    start, end, current = found[0]
    # Package metadata may itself have a version field; only the package's
    # leading table owns the version of the local distribution.
    nested = re.search(r"(?m)^\[(?!\[package\]\]).*\]", text[start:end])
    boundary = start + nested.start() if nested is not None else end
    updated = replace_version(
        text[start:boundary], current, target, "local flexi package"
    )
    return current, text[:start] + updated + text[boundary:]


def badge_metadata(text: str, target: str) -> tuple[str, str]:
    matches = tuple(BADGE.finditer(text))
    if len(matches) != 1:
        message = "README.md must contain exactly one /badge/version-X.Y.Z- badge"
        raise ReleaseError(message)
    match = matches[0]
    current = version(match["version"])
    start, end = match.span("version")
    return current, text[:start] + target + text[end:]


def changelog_sections(text: str) -> Iterator[tuple[int, int, str]]:
    """Find second-level headings, excluding fenced Markdown examples."""
    offset = 0
    fence = ""
    for line in text.splitlines(keepends=True):
        marker = re.match(r" {0,3}(`{3,}|~{3,})", line)
        if marker is not None:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not line[marker.end() :].strip():
                fence = ""
        elif not fence and (heading := re.fullmatch(r"##[ \t]+([^\r\n]+)\r?\n?", line)):
            yield offset, offset + len(line.rstrip("\r\n")), heading[1].strip()
        offset += len(line)


def changelog_metadata(text: str, target: str, *, prepare: bool) -> str:
    sections = tuple(changelog_sections(text))
    released = [
        section
        for section in sections
        if re.fullmatch(
            rf"(?:{re.escape(target)}|\[{re.escape(target)}\])"
            r"(?: - \d{4}-\d{2}-\d{2})?",
            section[2],
        )
    ]
    pending = [
        section for section in sections if section[2] in {"Unreleased", "[Unreleased]"}
    ]
    if len(released) > 1 or len(pending) > 1:
        message = "CHANGELOG.md contains duplicate release or Unreleased headings"
        raise ReleaseError(message)
    chosen = released or (pending if prepare else [])
    if not chosen:
        message = (
            f"CHANGELOG.md needs a ## {target} section "
            "or an Unreleased section with notes"
            if prepare
            else f"CHANGELOG.md has no ## {target} release section; run prepare first"
        )
        raise ReleaseError(message)
    start, end, _heading = chosen[0]
    following = next(
        (position for position, _, _ in sections if position > start), len(text)
    )
    notes = re.sub(r"<!--.*?-->", "", text[end:following], flags=re.DOTALL)
    notes = re.sub(r"(?m)^#{1,6}[ \t]+[^\r\n]*", "", notes).strip()
    if not notes:
        message = (
            "CHANGELOG.md release notes are empty; "
            "describe the changes before preparing"
        )
        raise ReleaseError(message)
    return text if released else text[:start] + f"## {target}" + text[end:]


def check_snapshots(root: Path, target: str) -> None:
    directory = root / "docs" / "shots"
    if directory.is_symlink() or (root / "docs").is_symlink():
        message = "Snapshot directories must not be symlinks"
        raise ReleaseError(message)
    visible = 0
    for path in sorted(directory.glob("*.txt")):
        document = read_document(root, path.relative_to(root).as_posix())
        for line in document.text.splitlines()[:3]:
            if "flexi" not in line:
                continue
            for found in re.finditer(r"(?<!\S)v(?P<version>\S+)", line):
                visible += 1
                if found["version"] != target:
                    message = (
                        f"{path.name} shows v{found['version']}; "
                        f"regenerate snapshots for v{target}"
                    )
                    raise ReleaseError(message)
    if not visible:
        message = "No visible version headers in docs/shots/*.txt; regenerate snapshots"
        raise ReleaseError(message)


def stage(document: Document, text: str, cleanup: ExitStack) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=".prepare-release-", dir=document.path.parent
    )
    path = Path(name)
    cleanup.callback(path.unlink, missing_ok=True)
    with os.fdopen(descriptor, "wb") as output:
        output.write(text.encode("utf-8"))
    path.chmod(document.mode)
    return path


def write_documents(documents: dict[str, Document], updates: dict[str, str]) -> int:
    """Stage all replacements first and restore originals if replacement fails."""
    changed = [name for name, text in updates.items() if documents[name].text != text]
    with ExitStack() as cleanup:
        replacements = {
            name: stage(documents[name], updates[name], cleanup) for name in changed
        }
        originals = {
            name: stage(documents[name], documents[name].text, cleanup)
            for name in changed
        }
        replaced: list[str] = []
        try:
            for name in changed:
                replacements[name].replace(documents[name].path)
                replaced.append(name)
        except BaseException:
            for name in reversed(replaced):
                originals[name].replace(documents[name].path)
            raise
    return len(changed)


def execute(root: Path, title: str, *, prepare: bool, snapshots: bool) -> None:
    target = release_version(title)
    if root.is_symlink():
        message = "Repository root must not be a symlink"
        raise ReleaseError(message)
    root = root.resolve(strict=True)
    documents = {name: read_document(root, name) for name in FILES}
    current, project = project_metadata(documents["pyproject.toml"].text, target)
    locked, lock = lock_metadata(documents["uv.lock"].text, target)
    badged, readme = badge_metadata(documents["README.md"].text, target)
    if version_key(target) < version_key(current):
        message = f"Refusing to downgrade project.version from {current} to {target}"
        raise ReleaseError(message)
    changelog = changelog_metadata(
        documents["CHANGELOG.md"].text, target, prepare=prepare
    )
    if prepare:
        changed = write_documents(
            documents,
            dict(zip(FILES, (project, lock, readme, changelog), strict=True)),
        )
        print(f"Prepared release {target}: {changed} files changed.")
    else:
        if (current, locked, badged) != (target, target, target):
            message = (
                f"Release metadata disagrees with {target}: "
                f"project={current}, lock={locked}, badge={badged}"
            )
            raise ReleaseError(message)
        if snapshots:
            check_snapshots(root, target)
        print(f"Release metadata matches {target}.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "check"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--root", required=True, type=Path)
        subparser.add_argument("--title", required=True)
        if command == "check":
            subparser.add_argument("--check-snapshots", action="store_true")
    args = parser.parse_args(argv)
    try:
        execute(
            args.root,
            args.title,
            prepare=args.command == "prepare",
            snapshots=getattr(args, "check_snapshots", False),
        )
    except (ReleaseError, OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        print(f"Release metadata error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
