"""Set Flexi up, and start again when that is what is meant.

``flexi init`` creates the database and asks the five questions; where there
are already records it shows what is there first.

Erasing is a line on the menu, not a flag. It appears only when there is
something to erase, says how many records it would take, and asks for a typed
word. Without a terminal there is no menu and no erasing: the command reports
what is there and stops.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import click
from rich.text import Text

from flexi.cli import ui
from flexi.domain.format import plural
from flexi.models.database.backup import read_only, snapshot, verify
from flexi.models.database.lease import (
    DatabaseBusyError,
    LeaseMode,
    database_lease,
)

__all__ = (
    "CONFIRM_WORD",
    "COUNTED",
    "READ_TIMEOUT",
    "Choice",
    "Contents",
    "ask",
    "confirm_reset",
    "describe",
    "options",
    "overview",
    "reset",
    "settled",
)

CONFIRM_WORD = "reset"

READ_TIMEOUT = 1.0
"""Seconds to wait for a locked database.

The application holds a write lock while it commits, and SQLite's default of
five seconds applies per table, so the five reads below would stall for half a
minute before saying anything."""

COUNTED: tuple[tuple[str, str], ...] = (
    ("clock events", "clock_events"),
    ("work sessions", "work_sessions"),
    ("booked absences", "absence_days"),
    ("balance adjustments", "balance_adjustments"),
    ("leave allowances", "leave_entitlements"),
)


class Choice(StrEnum):
    """What can be done about a Flexi that is already set up."""

    OPEN = "open"
    SETTINGS = "settings"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class Contents:
    """What a database holds, for a prompt that has to be specific.

    ``unreadable`` is not empty: a locked or damaged file may still hold every
    record.
    """

    counts: tuple[tuple[str, int], ...] = ()
    unreadable: bool = False

    @property
    def total(self) -> int:
        return sum(count for _, count in self.counts)

    @property
    def is_empty(self) -> bool:
        return not self.unreadable and self.total == 0


def describe(db_path: Path) -> Contents:
    """Count the rows a reset would take, in reading order."""
    if not db_path.is_file():
        # A file that is not there holds nothing; unreadable is a different
        # answer.
        return Contents()

    counts: list[tuple[str, int]] = []
    try:
        # `closing`, not the bare connection: `with sqlite3.connect(...)` opens
        # a transaction and leaves the handle open, and Windows refuses to
        # delete a file anything still has open.
        with closing(read_only(db_path, timeout=READ_TIMEOUT)) as connection:
            # Probe first: "not a database" and "no such table" both arrive as
            # DatabaseError, and the loop below forgives the second because
            # COUNTED is maintained by hand. Without the probe a corrupt file
            # would be described as empty.
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()

            for label, table in COUNTED:
                try:
                    row = connection.execute(
                        f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed names
                    ).fetchone()
                except sqlite3.DatabaseError:
                    # One missing table is a schema older or newer than this
                    # list, not an unreadable file. The rest still counts.
                    continue
                if row and row[0]:
                    counts.append((label, row[0]))
    except sqlite3.DatabaseError:
        return Contents(unreadable=True)
    return Contents(tuple(counts))


def overview(db_path: Path, contents: Contents) -> list[Text]:
    """The block that opens the rail: where the records are, and what they are."""
    lines = [
        ui.wordmark(),
        ui.body(),
        ui.step("Already set up", tone=ui.Tone.DONE, marker="●"),
        ui.body(str(db_path)),
        ui.body(),
    ]
    if contents.unreadable:
        lines.append(ui.body("This database could not be read.", style="bold"))
        lines.append(ui.body("It may still hold records."))
    elif contents.is_empty:
        lines.append(ui.body("Nothing recorded yet."))
    else:
        lines.extend(ui.measure(count, label) for label, count in contents.counts)
    lines.append(ui.body())
    return lines


def options(contents: Contents) -> list[ui.Option[Choice]]:
    """The three things to do about a Flexi that is already here.

    An unreadable database counts at nothing, so the grave line names what it
    would take in words: "erase 0 records" rounds unknown to zero.
    """
    total = contents.total
    if contents.unreadable:
        erase = "erase whatever it holds"
    elif contents.is_empty:
        erase = "erase everything"
    else:
        erase = f"erase {total} {plural(total, 'record')}"
    return [
        ui.Option(Choice.OPEN, "Open Flexi", "your records, as they are"),
        ui.Option(
            Choice.SETTINGS, "Change settings", "leave year, working days, region"
        ),
        ui.Option(Choice.RESET, "Start again", erase, grave=True),
    ]


def ask(db_path: Path, contents: Contents) -> Choice | None:
    """Show what is there, and return what was chosen about it."""
    ui.write(overview(db_path, contents))
    picked = ui.choose("What would you like to do?", options(contents))
    return picked.value if picked is not None else None


def confirm_reset(contents: Contents) -> bool:
    """The last gate. Says what goes, then asks for the word."""
    grave = ui.Tone.GRAVE
    lines = [
        ui.body(),
        ui.step(
            "This erases everything and cannot be undone",
            tone=grave,
            marker="▲",
        ),
        ui.body(tone=grave),
    ]
    if contents.unreadable:
        lines.append(
            ui.body("This database could not be read.", tone=grave, style="bold")
        )
        lines.append(ui.body("It may hold more than is listed here.", tone=grave))
    else:
        lines.extend(
            ui.measure(count, label, tone=grave) for label, count in contents.counts
        )
    lines.extend(
        [
            ui.body(tone=grave),
            ui.body("A verified snapshot is written to the backups", tone=grave),
            ui.body("directory first. Nothing else brings these back.", tone=grave),
            ui.body(tone=grave),
        ]
    )
    ui.write(lines)
    return ui.type_the_word(CONFIRM_WORD, f"Type {CONFIRM_WORD!r} to continue")


def settled(message: str) -> None:
    """Close the rail with something having happened."""
    ui.write([ui.step(message, tone=ui.Tone.DONE, marker="●"), ui.tail()])


def reset(db_path: Path) -> Path | None:
    """Snapshot, verify, then remove the database file and nothing else.

    The backups directory sits inside the data directory, so removing the
    directory would take every snapshot with it. A symlink is refused:
    ``is_file`` and ``sqlite3.connect`` resolve one where ``Path.unlink`` does
    not, so erasing through a link would remove the link and keep the records.
    """
    if db_path.is_symlink():
        msg = (
            f"{db_path} is a link to {db_path.resolve()}. Flexi will not erase "
            "through a link. Nothing was deleted: remove the link, or run the "
            "reset against the file it points at."
        )
        raise click.ClickException(msg)

    try:
        with database_lease(db_path, LeaseMode.EXCLUSIVE):
            taken: Path | None = None
            if db_path.is_file():
                taken = snapshot(db_path)
                if not verify(taken):
                    msg = (
                        f"The snapshot at {taken} did not verify. Nothing was deleted."
                    )
                    raise click.ClickException(msg)
                db_path.unlink()
            return taken
    except DatabaseBusyError as error:
        raise click.ClickException(str(error)) from error
