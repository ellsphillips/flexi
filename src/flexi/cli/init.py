"""Setting Flexi up, and starting again when that is really what is meant.

``flexi init`` on a clean machine creates the database and asks the five
questions. On a machine that already has records it says so and stops, because
the only other thing it could do is destroy them.

``flexi init --reset`` is that other thing. It is the one command in Flexi that
loses data, so it counts what it is about to take, says it out loud, takes a
verified snapshot first, and asks for a word rather than a keystroke. It refuses
outright without a terminal: a prompt reads stdin, and ``yes | flexi init
--reset`` would otherwise wipe a year of records with nobody present.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from flexi.models.database.backup import snapshot, verify

CONFIRM_WORD = "reset"

COUNTED: tuple[tuple[str, str], ...] = (
    ("clock events", "clock_events"),
    ("work sessions", "work_sessions"),
    ("booked absences", "absence_days"),
    ("balance adjustments", "balance_adjustments"),
    ("leave allowances", "leave_entitlements"),
)


@dataclass(frozen=True, slots=True)
class Contents:
    """What a database holds, for a prompt that has to be specific."""

    counts: tuple[tuple[str, int], ...]

    @property
    def total(self) -> int:
        return sum(count for _, count in self.counts)

    @property
    def is_empty(self) -> bool:
        return self.total == 0


def describe(db_path: Path) -> Contents:
    """Count the rows a reset would take, in reading order."""
    counts: list[tuple[str, int]] = []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            for label, table in COUNTED:
                try:
                    row = connection.execute(
                        f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed names
                    ).fetchone()
                except sqlite3.DatabaseError:
                    continue
                if row and row[0]:
                    counts.append((label, row[0]))
    except sqlite3.DatabaseError:
        return Contents(())
    return Contents(tuple(counts))


def interactive() -> bool:
    """A real terminal on both ends.

    click.confirm reads stdin, not the terminal, so a pipe answers for the
    user. Nothing destructive happens without somebody there to say so.
    """
    return sys.stdin.isatty() and sys.stderr.isatty()


def confirm_reset(db_path: Path, contents: Contents) -> bool:
    """Say what will be lost, then require the word rather than a keystroke."""
    click.secho("This will erase everything Flexi has recorded.", fg="red", bold=True)
    click.echo(f"  {db_path}")
    if contents.is_empty:
        click.echo("  (nothing recorded yet)")
    else:
        for label, count in contents.counts:
            click.echo(f"  {count:>6}  {label}")
    click.echo()
    click.echo("A snapshot is taken first, and kept in the backups directory.")
    click.echo("Nothing else can undo this.")
    click.echo()

    typed: str = click.prompt(
        f"Type {CONFIRM_WORD!r} to continue, or anything else to stop",
        default="",
        show_default=False,
    )
    return typed.strip().lower() == CONFIRM_WORD


def reset(db_path: Path) -> Path | None:
    """Snapshot, verify, then remove the database and nothing else.

    Only the file. The backups directory lives inside the data directory, so
    deleting the directory would take every snapshot ever made -- including the
    one taken a moment earlier, which is the whole safety net.
    """
    taken: Path | None = None
    if db_path.is_file():
        taken = snapshot(db_path)
        if not verify(taken):
            msg = f"The snapshot at {taken} did not verify. Nothing was deleted."
            raise click.ClickException(msg)
        db_path.unlink()
    return taken
