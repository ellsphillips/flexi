"""Release metadata edits stay local, selective, and safe to repeat."""

from __future__ import annotations

import runpy
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prepare_release.py"
FILES = ("pyproject.toml", "uv.lock", "README.md", "CHANGELOG.md")
type EntryPoint = Callable[[Sequence[str] | None], int]

pytestmark = pytest.mark.skipif(not SCRIPT.is_file(), reason="sdist")


@pytest.fixture
def release() -> EntryPoint:
    return cast("EntryPoint", runpy.run_path(str(SCRIPT))["main"])


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    documents = {
        "pyproject.toml": (
            '[project] # distribution\nname = "flexi"\n'
            "version = '0.2.0' # keep this comment\n\n"
            '[tool.example]\nversion = "0.2.0"\n'
        ),
        "uv.lock": (
            'version = 1\n\n[[package]]\nname = "dependency"\n'
            'version = "0.2.0"\nsource = { registry = "https://example.com" }\n\n'
            '[[package]] # local project\nname = "flexi"\nversion = "0.2.0"\n'
            'source = { editable = "." }\n\n[package.metadata]\n'
            'version = "leave this alone"\n\n[[package]]\nname = "flexi"\n'
            'version = "9.0.0"\nsource = { registry = "https://example.com" }\n'
        ),
        "README.md": (
            "# flexi\n\n"
            "[![version](https://shieldcn.dev/badge/version-0.2.0-00AAAD.svg"
            "?variant=outline)](https://pypi.org/project/flexi/)\n\n"
            "Install flexi>=0.2.0; the 0.2.0 examples remain supported.\n"
        ),
        "CHANGELOG.md": (
            "# Changelog\n\n## [Unreleased]\n\n"
            "### Fixed\n\n- Keep release metadata consistent.\n\n"
            "## 0.2.0\n\n- Initial release.\n"
        ),
    }
    for name, text in documents.items():
        (tmp_path / name).write_text(text, encoding="utf-8", newline="")
    return tmp_path


def contents(root: Path) -> dict[str, bytes]:
    return {name: (root / name).read_bytes() for name in FILES}


def arguments(
    root: Path, version: str = "0.3.0", command: str = "prepare"
) -> list[str]:
    return [command, "--root", str(root), "--title", f"chore(release): {version}"]


def test_prepare_updates_only_release_fields_and_is_idempotent(
    checkout: Path, release: EntryPoint, capsys: pytest.CaptureFixture[str]
) -> None:
    before = contents(checkout)
    assert release(arguments(checkout)) == 0
    after = contents(checkout)
    assert after == {
        "pyproject.toml": before["pyproject.toml"].replace(
            b"version = '0.2.0'", b"version = '0.3.0'"
        ),
        "uv.lock": before["uv.lock"].replace(
            b'name = "flexi"\nversion = "0.2.0"',
            b'name = "flexi"\nversion = "0.3.0"',
        ),
        "README.md": before["README.md"].replace(
            b"/badge/version-0.2.0-", b"/badge/version-0.3.0-"
        ),
        "CHANGELOG.md": before["CHANGELOG.md"].replace(b"## [Unreleased]", b"## 0.3.0"),
    }
    timestamps = {name: (checkout / name).stat().st_mtime_ns for name in FILES}
    assert release(arguments(checkout)) == 0
    assert release(arguments(checkout, command="check")) == 0
    assert contents(checkout) == after
    assert {name: (checkout / name).stat().st_mtime_ns for name in FILES} == timestamps
    assert "0 files changed" in capsys.readouterr().out
    locked = tomllib.loads(after["uv.lock"].decode())
    assert locked["version"] == 1
    assert [package["version"] for package in locked["package"]] == [
        "0.2.0",
        "0.3.0",
        "9.0.0",
    ]


@pytest.mark.parametrize("heading", ["## Unreleased", "## [Unreleased]"])
def test_prepare_preserves_crlf_and_comments(
    checkout: Path, release: EntryPoint, heading: str
) -> None:
    for name, raw in contents(checkout).items():
        (checkout / name).write_bytes(
            raw.replace(b"## [Unreleased]", heading.encode()).replace(b"\n", b"\r\n")
        )
    assert release(arguments(checkout)) == 0
    for raw in contents(checkout).values():
        assert b"\n" not in raw.replace(b"\r\n", b"")
    assert b"# keep this comment\r\n" in (checkout / "pyproject.toml").read_bytes()


@pytest.mark.parametrize("heading", ["## 0.2.0", "## [0.2.0] - 2026-09-20"])
def test_existing_release_notes_are_preserved(
    checkout: Path, release: EntryPoint, heading: str
) -> None:
    (checkout / "CHANGELOG.md").write_text(
        f"# Changelog\n\n{heading}\n\n- Initial release.\n", encoding="utf-8"
    )
    before = contents(checkout)
    assert release(arguments(checkout, "0.2.0")) == 0
    assert release(arguments(checkout, "0.2.0", "check")) == 0
    assert contents(checkout) == before


@pytest.mark.parametrize(
    "title",
    [
        "chore(release): v0.3.0",
        "chore(release): 0.3.0rc1",
        "chore(release): 0.3.0-rc.1",
        "chore(release): 0.3.0+local",
        "chore(release): 00.3.0",
        "chore(release): 0.03.0",
        "chore(release): 0.3.00",
        "chore(release): 0.3",
        "chore(release): ٠.3.0",
        "chore(release): -1.3.0",
        "chore(release): 0.3.0\n",
        " chore(release): 0.3.0",
        "chore(release): 0.3.0 ",
        "chore(release):  0.3.0",
        "chore(release): 0.3.0; touch injected",
        "$(touch injected)",
        "fix: chore(release): 0.3.0",
        "chore(release): 0.3.0\nchore(release): 0.4.0",
    ],
)
def test_adversarial_titles_fail_before_changing_files(
    checkout: Path, release: EntryPoint, title: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = contents(checkout)
    assert release(["prepare", "--root", str(checkout), "--title", title]) == 1
    assert contents(checkout) == before
    assert "Title must be exactly" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("0.2.0", "0.1.9"),
        ("1.10.0", "1.9.99"),
        ("2.0.0", "1.999.999"),
    ],
)
def test_downgrade_is_rejected_numerically(
    checkout: Path,
    release: EntryPoint,
    current: str,
    target: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = checkout / "pyproject.toml"
    project.write_bytes(
        project.read_bytes().replace(b"'0.2.0'", repr(current).encode())
    )
    before = contents(checkout)
    assert release(arguments(checkout, target)) == 1
    assert contents(checkout) == before
    assert "Refusing to downgrade" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["0.10.0", "10.0.0", "9" * 5000 + ".0.0"])
def test_version_comparison_has_no_fixed_integer_limit(
    checkout: Path, release: EntryPoint, target: str
) -> None:
    assert release(arguments(checkout, target)) == 0
    assert release(arguments(checkout, target, "check")) == 0


@pytest.mark.parametrize(
    ("name", "replacement", "message"),
    [
        ("pyproject.toml", '[project]\nname="other"\nversion="0.2.0"\n', "flexi"),
        ("pyproject.toml", '[project]\nname="flexi"\nversion="00.2.0"\n', "SemVer"),
        ("pyproject.toml", "[project\n", "Release metadata error"),
        ("uv.lock", 'version=1\n[[package]]\nname="other"\nversion="0.2.0"\n', "local"),
        ("README.md", "# No badge\n", "exactly one"),
        (
            "README.md",
            "/badge/version-0.2.0-A.svg /badge/version-0.2.0-B.svg",
            "exactly one",
        ),
        ("CHANGELOG.md", "# Changelog\n\n## 0.2.0\n\n- Initial.\n", "Unreleased"),
        ("CHANGELOG.md", "## Unreleased\n\n### Fixed\n\n<!-- TODO -->\n", "empty"),
        (
            "CHANGELOG.md",
            "## Unreleased\n- Note\n## [Unreleased]\n- Note\n",
            "duplicate",
        ),
        ("CHANGELOG.md", "## 0.3.0\n- Note\n## [0.3.0]\n- Note\n", "duplicate"),
        ("CHANGELOG.md", "```markdown\n## Unreleased\n- Example\n```\n", "Unreleased"),
    ],
)
def test_invalid_metadata_leaves_every_file_untouched(
    checkout: Path,
    release: EntryPoint,
    name: str,
    replacement: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (checkout / name).write_text(replacement, encoding="utf-8")
    before = contents(checkout)
    assert release(arguments(checkout)) == 1
    assert contents(checkout) == before
    assert message in capsys.readouterr().err


def test_duplicate_local_lock_packages_fail_without_modification(
    checkout: Path, release: EntryPoint
) -> None:
    lock = checkout / "uv.lock"
    lock.write_bytes(
        lock.read_bytes()
        + (b'\n[[package]]\nname="flexi"\nversion="0.2.0"\nsource={editable="."}\n')
    )
    before = contents(checkout)
    assert release(arguments(checkout)) == 1
    assert contents(checkout) == before


def test_markdown_examples_do_not_end_real_release_notes(
    checkout: Path, release: EntryPoint
) -> None:
    changelog = checkout / "CHANGELOG.md"
    changelog.write_text(
        "## Unreleased\n\n- Explain headings:\n\n~~~~markdown\n"
        "## Unreleased\n~~~\n## Example\n~~~~\n\n## 0.2.0\n- Initial.\n",
        encoding="utf-8",
    )
    before = changelog.read_bytes()
    assert release(arguments(checkout)) == 0
    assert changelog.read_bytes() == before.replace(b"## Unreleased", b"## 0.3.0", 1)


@pytest.mark.parametrize("name", FILES)
def test_symlink_metadata_cannot_modify_outside_files(
    checkout: Path, release: EntryPoint, name: str
) -> None:
    path = checkout / name
    outside = checkout.parent / f"outside-{name}"
    original = path.read_bytes()
    path.replace(outside)
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("This system does not permit creating symlinks")
    before = contents(checkout)
    assert release(arguments(checkout)) == 1
    assert outside.read_bytes() == original
    assert contents(checkout) == before


def test_symlink_root_is_rejected(checkout: Path, release: EntryPoint) -> None:
    link = checkout.parent / "linked-repository"
    try:
        link.symlink_to(checkout, target_is_directory=True)
    except OSError:
        pytest.skip("This system does not permit creating symlinks")
    before = contents(checkout)
    assert release(arguments(link)) == 1
    assert contents(checkout) == before


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid-utf8"])
def test_unreadable_metadata_fails_before_any_writes(
    checkout: Path, release: EntryPoint, kind: str
) -> None:
    path = checkout / "CHANGELOG.md"
    before = {name: (checkout / name).read_bytes() for name in FILES[:-1]}
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xff")
    assert release(arguments(checkout)) == 1
    assert {name: (checkout / name).read_bytes() for name in FILES[:-1]} == before


def test_staging_failure_leaves_originals_and_cleans_temporary_files(
    checkout: Path, release: EntryPoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = contents(checkout)
    create = tempfile.mkstemp
    calls = 0

    def failing_create(**kwargs: str | Path) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            message = "Disk is full"
            raise OSError(message)
        return create(prefix=str(kwargs["prefix"]), dir=kwargs["dir"])

    monkeypatch.setattr(tempfile, "mkstemp", failing_create)
    assert release(arguments(checkout)) == 1
    assert contents(checkout) == before
    assert not list(checkout.glob(".prepare-release-*"))


def test_replacement_failure_restores_already_replaced_files(
    checkout: Path, release: EntryPoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = contents(checkout)
    replace = Path.replace
    calls = 0

    def failing_replace(self: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            message = "Replacement failed"
            raise OSError(message)
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    assert release(arguments(checkout)) == 1
    assert contents(checkout) == before
    assert not list(checkout.glob(".prepare-release-*"))


@pytest.mark.parametrize("name", ["pyproject.toml", "uv.lock", "README.md"])
def test_check_rejects_disagreement_without_repairing_it(
    checkout: Path,
    release: EntryPoint,
    name: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert release(arguments(checkout)) == 0
    path = checkout / name
    path.write_bytes(path.read_bytes().replace(b"0.3.0", b"0.2.0"))
    before = contents(checkout)
    assert release(arguments(checkout, command="check")) == 1
    assert contents(checkout) == before
    assert "disagrees" in capsys.readouterr().err


def test_check_requires_a_target_release_changelog_section(
    checkout: Path, release: EntryPoint, capsys: pytest.CaptureFixture[str]
) -> None:
    before = contents(checkout)
    assert release(arguments(checkout, command="check")) == 1
    assert contents(checkout) == before
    assert "run prepare first" in capsys.readouterr().err


def test_snapshots_check_visible_headers_and_allow_clipped_tiny_shots(
    checkout: Path, release: EntryPoint, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release(arguments(checkout)) == 0
    shots = checkout / "docs" / "shots"
    shots.mkdir(parents=True)
    full = shots / "dashboard-full.txt"
    full.write_text("\n flexi·                         v0.3.0 \n", encoding="utf-8")
    (shots / "dashboard-tiny.txt").write_text("\n flexi·    \n", encoding="utf-8")
    args = [*arguments(checkout, command="check"), "--check-snapshots"]
    assert release(args) == 0
    full.write_text("\n flexi·                         v0.2.0 \n", encoding="utf-8")
    assert release(args) == 1
    assert "dashboard-full.txt shows v0.2.0" in capsys.readouterr().err


def test_missing_snapshots_are_only_an_error_when_explicitly_checked(
    checkout: Path, release: EntryPoint, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release(arguments(checkout)) == 0
    assert release(arguments(checkout, command="check")) == 0
    assert release([*arguments(checkout, command="check"), "--check-snapshots"]) == 1
    assert "No visible version headers" in capsys.readouterr().err


def test_snapshot_directory_symlink_is_rejected(
    checkout: Path, release: EntryPoint, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release(arguments(checkout)) == 0
    outside = checkout.parent / "outside-docs"
    outside.mkdir()
    try:
        (checkout / "docs").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This system does not permit creating symlinks")
    assert release([*arguments(checkout, command="check"), "--check-snapshots"]) == 1
    assert "must not be symlinks" in capsys.readouterr().err


def test_command_line_entrypoint_returns_validation_status(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *arguments(checkout)])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert result.value.code == 0
