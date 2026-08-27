"""Setting Flexi up, and starting again when that is really what is meant.

``flexi init`` on a clean machine creates the database and asks the five
questions. On a machine that already has records it shows what is there and
offers what can be done about it -- including erasing the lot, which is the one
thing in Flexi that loses data.

That option is not a flag. ``--reset`` sat in the help text of a command most
people run once, where the only two ways to meet it were to go looking or to
find it by accident, and neither is how somebody should arrive at deleting a
year of records. It is a line on a menu that appears only when there is
something to erase, it is drawn in the deficit red, it says how many records it
would take, and it asks for a word rather than a keystroke.

Without a terminal there is no menu and no erasing: the command reports what is
there and stops. There is deliberately no way to erase Flexi's records without a
person present to type the word.
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
from flexi.models.database.backup import snapshot, verify
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
"""The module's complete setup and reset vocabulary."""

CONFIRM_WORD = "reset"

READ_TIMEOUT = 1.0
"""Seconds to wait for a locked database.

The application holds a write lock while it commits. Waiting the SQLite default
of five seconds per table turns "what is in here?" into a half-minute stall with
nothing on screen, and the answer to a database that is busy is to say so."""

COUNTED: tuple[tuple[str, str], ...] = (
    ("clock events", "clock_events"),
    ("work sessions", "work_sessions"),
    ("booked absences", "absence_days"),
    ("balance adjustments", "balance_adjustments"),
    ("leave allowances", "leave_entitlements"),
)


class Choice(StrEnum):
    """What somebody can do about a Flexi that is already set up."""

    OPEN = "open"
    SETTINGS = "settings"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class Contents:
    """What a database holds, for a prompt that has to be specific.

    ``unreadable`` is not the same as empty, and conflating them is how a
    confirmation ends up reassuring somebody that there is nothing to lose while
    a locked or damaged file sits there full of records.
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
        # Absent is not the same as unreadable. There is genuinely nothing to
        # lose here, and saying so is the truthful answer rather than a hedge.
        return Contents()

    counts: list[tuple[str, int]] = []
    try:
        # `closing`, not the bare connection: `with sqlite3.connect(...)` is a
        # transaction and leaves the handle open, and this runs immediately
        # before the reset that deletes the file. Windows refuses to delete a
        # file anything still has open.
        with closing(
            sqlite3.connect(
                f"{db_path.absolute().as_uri()}?mode=ro",
                uri=True,
                timeout=READ_TIMEOUT,
            )
        ) as connection:
            # Ask whether the file is a database at all before asking what is
            # in it. "not a database" and "no such table" both arrive as
            # DatabaseError, and the loop below has to forgive the second --
            # COUNTED is maintained by hand, so a table renamed by a later
            # migration must not blank the count. Without this probe that
            # forgiveness swallows the first too, and a corrupt file holding a
            # year of records is described to its owner as empty.
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
    total = contents.total
    erase = (
        "erase everything"
        if contents.is_empty
        else f"erase {total} {plural(total, 'record')}"
    )
    return [
        ui.Option(Choice.OPEN, "Open Flexi", "your records, as they are"),
        ui.Option(Choice.SETTINGS, "Change settings", "leave year, hours, region"),
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
    """Snapshot, verify, then remove the database and nothing else.

    Only the file. The backups directory lives inside the data directory, so
    deleting the directory would take every snapshot ever made -- including the
    one taken a moment earlier, which is the whole safety net.
    """
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
