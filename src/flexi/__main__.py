"""The command line entry point.

Textual, Alembic, SQLAlchemy and httpx are imported inside the commands that
need them, so `flexi --version` and `--help` stay cheap.
"""

from __future__ import annotations

import functools
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import click

from flexi import wallclock
from flexi.cli import TypedDate, Utf8Text
from flexi.locations import backups_directory, database_file
from flexi.services.setup import is_initialised

if TYPE_CHECKING:
    from flexi.app import FlexiApp as FlexiApplication
    from flexi.services.registry import Services as ServiceRegistry
else:

    class FlexiApplication(Protocol):
        """Runtime-resolvable application result without an eager Textual import."""

        @property
        def return_code(self) -> int | None:
            """The code the application exited with, once it has exited."""

        def run(self) -> object:
            """Run the application."""

    class ServiceRegistry(Protocol):
        """Runtime annotation for the lazily imported concrete registry."""


__all__ = (
    "NEEDS_TERMINAL",
    "NOT_INITIALISED",
    "UNREADABLE",
    "FlexiApplication",
    "ServiceRegistry",
    "already_set_up",
    "as_of_option",
    "ask_the_questions",
    "balance",
    "balance_log",
    "balance_show",
    "balance_undo",
    "balance_zero",
    "cli",
    "clock",
    "clock_in",
    "clock_out",
    "erase",
    "holidays",
    "holidays_refresh",
    "init",
    "launch",
    "leave",
    "migrate",
    "needs_a_terminal",
    "open_app",
    "open_database",
    "requires_setup",
    "run_app",
    "run_demo",
    "set_up_here",
    "unreadable",
)


@click.group(invoke_without_command=True)
# `package_name` lets Click read the version inside the flag's own callback; a
# literal `message` would read the metadata at import, on every command.
@click.version_option(
    None, "-v", "--version", package_name="flexi", message="%(version)s"
)
@click.option(
    "--demo",
    is_flag=True,
    help="Run against a throwaway database holding a plausible working life "
    "up to today.",
)
@click.pass_context
def cli(ctx: click.Context, *, demo: bool = False) -> None:
    """Track flexitime from the terminal."""
    from flexi.cli import output
    from flexi.config import CONFIG_PROBLEM

    output.prepare(ctx)

    # Not at module scope: `flexi.config` costs pydantic, and `--version` and
    # `--help` are answered during parsing and never reach this callback.
    if CONFIG_PROBLEM:
        click.secho(CONFIG_PROBLEM, fg="yellow", err=True)

    if demo and ctx.invoked_subcommand is not None:
        msg = "--demo opens the sample application; it does not take a command."
        raise click.UsageError(msg)
    if demo:
        needs_a_terminal(ctx)
        run_demo(ctx)
        return

    # The group callback runs before click resolves the subcommand, so a guard
    # here would refuse `flexi init`. Each command opens its own database.
    if ctx.invoked_subcommand is not None:
        return

    # Bare `flexi` on a new machine sets itself up; the setup guard is there to
    # stop clock, leave and balance answering from unchosen defaults.
    migrate()
    if not set_up_here():
        ask_the_questions(ctx, database_file())
        return
    needs_a_terminal(ctx)
    run_app(ctx, launch())


NOT_INITIALISED = (
    "Flexi is not set up on this machine yet.\n"
    "Run `flexi init` to choose your leave year, working days and bank holidays."
)

NEEDS_TERMINAL = (
    "Flexi is a full-screen application and needs a terminal.\n"
    "Try `flexi balance show`, or `flexi --help` for the rest."
)

UNREADABLE = (
    "The database at {path} could not be read.\n"
    "Move it aside, or restore a copy from {backups}, then run `flexi init`."
)


def needs_a_terminal(ctx: click.Context) -> None:
    """Refuse when there is no terminal for the application to draw on.

    Textual reads ``sys.__stdin__`` and draws on ``sys.__stderr__``, which is
    what :func:`flexi.cli.ui.interactive` checks. Left to run without one, the
    application never returns and holds the database lease.
    """
    from flexi.cli import ui

    if not ui.interactive():
        click.secho(NEEDS_TERMINAL, fg="yellow", err=True)
        ctx.exit(1)


def unreadable() -> click.ClickException:
    """The one sentence for a database Flexi cannot read."""
    return click.ClickException(
        UNREADABLE.format(path=database_file(), backups=backups_directory())
    )


def set_up_here() -> bool:
    """Whether this machine has a Flexi, with a damaged database raising.

    A missing, empty or unstamped database answers False. A file that is not a
    database, or one whose pages are torn, raises instead: the records are
    still there, and `flexi init` would start again over them.
    """
    try:
        return is_initialised()
    except sqlite3.DatabaseError as error:
        raise unreadable() from error


def run_app(ctx: click.Context, app: FlexiApplication) -> None:
    """Run the application, and carry what it exited with out to the shell.

    Textual sets a return code of 1 when a screen raises and prints the
    traceback itself, so the code has to be forwarded to the shell.
    """
    app.run()
    if app.return_code:
        ctx.exit(app.return_code)


def migrate() -> None:
    """Bring the schema to head, or say in one sentence what stopped it.

    The failures caught here are about the file: another Flexi holding it, an
    unwritable data directory, a file that is not a database, a schema this
    version cannot read. Anything else still raises.
    """
    from sqlalchemy.exc import DatabaseError

    from flexi.models.database.migrate import run_migrations

    try:
        run_migrations()
    except (sqlite3.DatabaseError, DatabaseError) as error:
        raise unreadable() from error
    except RuntimeError as error:
        # These messages are already readable, but only some name the file.
        message = str(error)
        if str(database_file()) not in message:
            message = f"{message}. The database is at {database_file()}."
        raise click.ClickException(message) from error
    except OSError as error:
        message = f"Flexi could not use {database_file().parent}: {error}."
        raise click.ClickException(message) from error


def as_of_option[ReturnT](
    help_text: str,
) -> Callable[[Callable[..., ReturnT]], Callable[..., ReturnT]]:
    """The ``--as-of`` option, declared once for the two commands that take it."""
    return click.option(
        "--as-of", "as_of", type=TypedDate(), default=None, help=help_text
    )


def requires_setup(
    *, fill: bool = True
) -> Callable[[Callable[..., int]], Callable[..., None]]:
    """Refuse before setup; migrate, open a session, and exit on what came back.

    Applied per command, not to the group, so `flexi init` and every `--help`
    stay reachable on a machine with no database. The decorated function takes
    the service registry and returns an exit code. ``fill=False`` is for the
    command whose own job is the fetch, so GOV.UK is not asked twice.
    """

    def decorate(command: Callable[..., int]) -> Callable[..., None]:
        @functools.wraps(command)
        @click.pass_context
        def guarded(ctx: click.Context, /, *args: object, **kwargs: object) -> None:
            if not set_up_here():
                click.secho(NOT_INITIALISED, fg="yellow", err=True)
                ctx.exit(1)
            services = open_database(ctx, fill=fill)
            ctx.exit(command(services, *args, **kwargs))

        return guarded

    return decorate


def launch(*, settings: bool = False, splash: bool = False) -> FlexiApplication:
    """Every way into the application goes through here.

    ``FlexiApp.__init__`` builds an engine, opens a session and reads the
    settings row, so the schema has to be at head before it is built.
    ``run_migrations`` returns as soon as it finds head, so migrating on every
    path costs one revision check and takes no extra backup.
    """
    from flexi.app import FlexiApp

    migrate()
    app = FlexiApp()
    app.open_settings = settings
    app.show_splash = splash
    return app


def open_database(ctx: click.Context, *, fill: bool = True) -> ServiceRegistry:
    """Migrate, connect, sweep, and hand back the service registry.

    Closing is registered on the context: `ctx.exit` raises, so a
    `session.close()` at the end of a command is unreachable after a failure.
    """
    from flexi.models.database.engine import database_scope
    from flexi.services.registry import build_services

    migrate()
    _engine, session = ctx.with_resource(database_scope())
    services = build_services(session)
    services.clock.sweep()
    if fill:
        services.bank_holidays.fill_if_empty()
    return services


@cli.group()
def holidays() -> None:
    """Look after the bank holiday calendar."""


@holidays.command(name="refresh")
@requires_setup(fill=False)
def holidays_refresh(services: ServiceRegistry) -> int:
    """Fetch the calendar for the configured region from GOV.UK."""
    from flexi.cli import holidays as holidays_cli

    return holidays_cli.run(services)


def run_demo(ctx: click.Context) -> None:
    """Launch against a temporary database holding the sample data.

    The seed is the one the screenshots use, anchored to today instead of to
    `samples.ANCHOR`: the demo opens on the real current week, which a fixed
    anchor would leave empty.
    """
    import tempfile
    from pathlib import Path

    from flexi.app import FlexiApp
    from flexi.models.database.db import Base
    from flexi.models.database.engine import database_scope
    from flexi.services.samples import seed_demo

    with tempfile.TemporaryDirectory(prefix="flexi-demo-") as directory:
        path = Path(directory) / "demo.db"
        with database_scope(path) as (engine, session):
            Base.metadata.create_all(engine)
            moment = wallclock.now()
            seed_demo(session, anchor=moment.date(), now=moment.time())
        run_app(ctx, FlexiApp(db_path=path))


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Set Flexi up on this machine.

    On a machine that already has records, this shows what is there and offers
    what can be done about it, including starting again.
    """
    db_path = database_file()

    if set_up_here():
        already_set_up(ctx, db_path)
        return

    migrate()
    if set_up_here():
        click.secho("Flexi is set up.", fg="green")
        return
    ask_the_questions(ctx, db_path, then_open=False)


def already_set_up(ctx: click.Context, db_path: Path) -> None:
    """Show what is recorded, and do what is chosen about it."""
    from flexi.cli import init as init_cli
    from flexi.cli import ui

    if not ui.interactive():
        # Headless is report and stop: no flag erases records without a prompt.
        click.echo(f"Flexi is set up. Its records are at {db_path}.")
        click.echo("Run `flexi init` from a terminal to change or reset them.")
        return

    contents = init_cli.describe(db_path)
    choice = init_cli.ask(db_path, contents)

    if choice is None:
        return
    if choice is init_cli.Choice.OPEN:
        open_app(ctx)
        return
    if choice is init_cli.Choice.SETTINGS:
        open_app(ctx, settings=True)
        return

    if not init_cli.confirm_reset(contents):
        ui.abandon("Nothing was erased.")
        return
    erase(db_path)
    ask_the_questions(ctx, db_path, then_open=False)


def open_app(ctx: click.Context, *, settings: bool = False) -> None:
    run_app(ctx, launch(settings=settings))


def erase(db_path: Path) -> None:
    """Snapshot, remove the records, and forget that this path was ever set up."""
    from flexi.cli import init as init_cli
    from flexi.services import setup as setup_service

    taken = init_cli.reset(db_path)
    setup_service.forget(db_path)
    if taken is not None:
        init_cli.settled(f"Erased. Snapshot kept at {taken}")


def ask_the_questions(
    ctx: click.Context, db_path: Path, *, then_open: bool = True
) -> None:
    """Open the setup form, which is a full screen and needs a terminal.

    ``then_open`` carries bare ``flexi`` on into the application once the
    questions are answered; ``flexi init`` stops and says so.
    """
    from flexi.cli import ui

    if not ui.interactive():
        click.secho(
            f"The database is ready at {db_path}, but setup needs answering.\n"
            "Run `flexi init` from a terminal to finish.",
            fg="yellow",
            err=True,
        )
        ctx.exit(1)

    run_app(ctx, launch(splash=True))

    if not set_up_here():
        click.echo("Setup was not completed.")
        ctx.exit(1)
    if not then_open:
        click.secho(f"Flexi is set up. Its records are at {db_path}.", fg="green")


@cli.group()
def clock() -> None:
    """Clock in or out."""


@clock.command(name="in")
@requires_setup()
def clock_in(services: ServiceRegistry) -> int:
    """Clock in to start a work session."""
    from flexi.cli import clock as clock_cli

    return clock_cli.clock_in(services)


@clock.command(name="out")
@requires_setup()
def clock_out(services: ServiceRegistry) -> int:
    """Clock out to end the current work session."""
    from flexi.cli import clock as clock_cli

    return clock_cli.clock_out(services)


@cli.command(
    context_settings={"ignore_unknown_options": True},
    short_help="Book or cancel leave in one line.",
)
@click.argument("words", nargs=-1, required=True, type=Utf8Text())
@click.option(
    "--note", default=None, type=Utf8Text(), help="A note, required for `other`."
)
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@click.option("--dry-run", is_flag=True, help="Show the plan and stop.")
@requires_setup()
def leave(
    services: ServiceRegistry,
    words: tuple[str, ...],
    note: str | None,
    *,
    yes: bool,
    dry_run: bool,
) -> int:
    """Book or cancel leave without opening the application.

    \b
    flexi leave annual friday
    flexi leave annual monday to friday
    flexi leave sick today pm
    flexi leave toil 12 jun
    flexi leave cancel next monday

    End with am, morning, pm or afternoon for half a day. Join two dates with
    to, until, through or `..`. The plan is shown before anything is written.
    """  # noqa: D301 - the \b is Click's, and a raw string breaks it
    from flexi.cli import leave as leave_cli

    return leave_cli.run(
        services,
        words,
        note=note,
        assume_yes=yes,
        dry_run=dry_run,
        today=wallclock.today(),
    )


@cli.group()
def balance() -> None:
    """Read and correct the flexi balance."""


@balance.command(name="show")
@as_of_option(
    "Report the balance as at the end of this date, which may not be in the "
    "future. Defaults to today."
)
@requires_setup()
def balance_show(services: ServiceRegistry, as_of: date | None) -> int:
    """Print the running balance and what it is made of."""
    from flexi.cli import balance as balance_cli

    return balance_cli.show(services, as_of)


@balance.command(name="zero")
@as_of_option("Settle up to and including this date. Defaults to yesterday.")
@click.option(
    "--reason",
    default=None,
    type=Utf8Text(),
    help="Why the balance was settled.",
)
@click.option("--yes", is_flag=True, help="Do not ask.")
@requires_setup()
def balance_zero(
    services: ServiceRegistry,
    as_of: date | None,
    reason: str | None,
    *,
    yes: bool,
) -> int:
    """Draw a line under everything up to a date."""
    from flexi.cli import balance as balance_cli

    return balance_cli.zero(services, as_of, reason, assume_yes=yes)


@balance.command(name="log")
@requires_setup()
def balance_log(services: ServiceRegistry) -> int:
    """List every correction ever recorded."""
    from flexi.cli import balance as balance_cli

    return balance_cli.log(services)


@balance.command(name="undo")
@click.argument("adjustment_id", type=int)
@requires_setup()
def balance_undo(services: ServiceRegistry, adjustment_id: int) -> int:
    """Remove a correction by its id, as listed by `flexi balance log`."""
    from flexi.cli import balance as balance_cli

    return balance_cli.undo(services, adjustment_id)


if __name__ == "__main__":
    cli()
