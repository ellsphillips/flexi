"""Every text file is read and written as UTF-8.

Without an ``encoding`` argument Python uses the locale's, which is cp1252 on
Windows: any character it cannot spell raises ``UnicodeDecodeError`` there and
nowhere else. Run Ruff's constructor-aware ``PLW1514`` check in isolation so its
preview requirement does not enable every preview rule under ``select = ALL``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SEARCHED = ("src", "tests", "scripts")


def _unencoded(*paths: str, source: str | None = None) -> list[str]:
    result = subprocess.run(  # noqa: S603 - fixed local linter and source paths
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--preview",
            "--select",
            "PLW1514",
            "--output-format",
            "json",
            "--stdin-filename",
            "encoding_fixture.py",
            *paths,
        ],
        input=source,
        capture_output=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=30,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    return [
        f"{item['filename']}:{item['location']['row']}"
        for item in json.loads(result.stdout)
    ]


def test_text_files_are_opened_with_an_encoding() -> None:
    offenders = _unencoded(*SEARCHED)

    assert offenders == [], (
        "these open text without saying UTF-8, so they read as cp1252 on "
        f"Windows: {offenders}"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("open('notes.txt')", 1),
        (
            (
                "from pathlib import Path as P\n"
                "path = P('notes.txt')\n"
                "path.open()\npath.read_text()\npath.write_text('notes')"
            ),
            3,
        ),
        (
            (
                "from pathlib import Path\n"
                "from urllib.request import build_opener\n"
                "reader = build_opener()\nreader.open(request)\n"
                "def read(reader: Path):\n    return reader.open()"
            ),
            1,
        ),
        (
            (
                "from urllib.request import build_opener as make_opener\n"
                "from zipfile import ZipFile as Archive\n"
                "reader = make_opener()\nreader.open(request, timeout=5)\n"
                "with Archive('archive.zip') as archive:\n"
                "    archive.open(archive.infolist()[0])"
            ),
            0,
        ),
        (
            (
                "from pathlib import Path\n"
                "open('notes.txt', encoding='utf-8')\n"
                "open('data.bin', 'rb')\n"
                "Path('data.bin').open('a+b')\n"
                "Path('notes.txt').read_text(encoding='utf-8')"
            ),
            0,
        ),
    ],
    ids=["builtin", "path-alias", "shadowed-reader", "binary-streams", "encoded"],
)
def test_encoding_guard_distinguishes_text_and_binary_apis(
    source: str, expected: int
) -> None:
    assert len(_unencoded("-", source=source)) == expected
