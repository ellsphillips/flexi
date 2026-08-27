"""The command line entry point.

Nothing heavy is imported at module scope. `flexi --version` used to load the
six Textual screens, alembic, SQLAlchemy and httpx -- 898 modules, most of a
second -- before printing a string it already had. The application, the
migration runner, the engine and the service registry are imported by the
functions that use them, so a command pays for what it does and no more.

`flexi.services.setup` is the model for this: its docstring says asking "am I
set up" should not cost the migration module, and it was the one place that
already knew.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

import click

from flexi import wallclock
from flexi.cli import TypedDate
from flexi.locations import database_file
from flexi.services.setup import is_initialised

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date
    from pathlib import Path

    from sqlalchemy import Engine
    from sqlalchemy.orm import Session

    from flexi.app import FlexiApp
    from flexi.services.registry import Services


T = TypeVar("T")


@click.group(invoke_without_command=True)
# `message=flexi.__version__` read the version at decoration time, which is to
# say at import, on every command. Click resolves `package_name` inside the
# flag's own callback instead, so the metadata is only read when asked for.
@click.version_option(
    None, "-v", "--version", package_name="flexi", message="%(version)s"
)
@click.option(
    "--demo",
    is_flag=True,
    help="Run against a throwaway database seeded with six weeks of a working life.",
)
@click.pass_context
def cli(ctx: click.Context, *, demo: bool = False) -> None:
    """Track flexitime from the terminal."""
    if demo and ctx.invoked_subcommand is not None:
        msg = "--demo opens the sample application; it does not take a command."
        raise click.UsageError(msg)
    if demo:
        run_demo()
        return

    ctx.ensure_object(dict)

    # Nothing is opened here. A guard in the group callback runs before click
    # has resolved the subcommand, so it would refuse `flexi init` on the very
    # machine that needs it, and block `flexi clock --help`. Each command opens
    # the database itself, through @requires_setup.
    if ctx.invoked_subcommand is not None:
        return

    # Bare `flexi` on a new machine sets itself up rather than refusing. The
    # guard exists to stop clock, leave and balance inventing answers from
    # defaults nobody chose -- not to make the application decline to open.
    from flexi.models.database.migrate import run_migrations

    run_migrations()
    if not is_initialised():
        ask_the_questions(ctx, database_file())
        return
    launch().run()


NOT_INITIALISED = (
    "Flexi is not set up on this machine yet.\n"
    "Run `flexi init` to choose your leave year, hours and bank holidays."
)


@dataclass(frozen=True, slots=True)
class Handles:
    """An open database, and the means to let go of it.

    Handed to the command by :func:`requires_setup` rather than fished back out
    of ``ctx.obj``, which Click types as ``Any`` -- thirteen accesses that
    ``mypy --strict`` could not check, and a rename away from failing at
    runtime. It stays on the context for ``ctx.call_on_close``.
    """

    engine: Engine
    session: Session
    services: Services

    def close(self) -> None:
        self.session.close()
        self.engine.dispose()


def as_of_option(help_text: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """The ``--as-of`` option, declared once for the two commands that take it.

    It was written out twice, differing only in the help string, and both
    copies used `click.DateTime` -- so both had to unwrap a `.date()` and
    declare a parameter as a `datetime` that could only ever be a date.
    """
    return click.option(
        "--as-of", "as_of", type=TypedDate(), default=None, help=help_text
    )


def requires_setup(command: Callable[..., int]) -> Callable[..., None]:
    """Refuse before setup; migrate, open a session, and exit on what came back.

    Applied per command rather than to the group, so `flexi init` and every
    `--help` remain reachable on a machine with no database.

    The decorated function takes the service registry and returns an exit code
    -- the shape every module in `flexi.cli` already has. It used to open the
    database and hand back nothing, so all eight commands repeated the same
    four lines to fish the registry back out of the context and turn a code
    into an exit, and `open_database`'s return value was dead.
    """

    @functools.wraps(command)
    @click.pass_context
    def guarded(ctx: click.Context, /, *args: object, **kwargs: object) -> None:
        if not is_initialised():
            click.secho(NOT_INITIALISED, fg="yellow", err=True)
            ctx.exit(1)
        ctx.exit(command(open_database(ctx).services, *args, **kwargs))

    return guarded


def launch(*, settings: bool = False, splash: bool = False) -> FlexiApp:
    """Every way into the application goes through here.

    ``FlexiApp.__init__`` builds an engine, opens a session and reads the settings
    row, so opening it against a database with no tables raises before a single
    screen is drawn. Leaving each caller to migrate first meant the invariant
    lived everywhere except where it was needed, and the reset path -- which
    deletes the database and then asks the five questions -- duly forgot.

    ``run_migrations`` returns as soon as it finds the schema already at head,
    so calling it on every path costs one revision check and takes no extra
    backup. That is a cheap price for the guarantee.
    """
    from flexi.app import FlexiApp
    from flexi.models.database.migrate import run_migrations

    run_migrations()
    app = FlexiApp()
    app.open_settings = settings
    app.show_splash = splash
    return app


def open_database(ctx: click.Context) -> Handles:
    """Migrate, connect, sweep, and hand back an open database.

    Closing is registered on the context rather than written at the end of each
    command. `ctx.exit` raises, so every hand-written `session.close()` after a
    failure was unreachable -- which is to say the session and the engine leaked
    on exactly the paths where something had already gone wrong.
    """
    from flexi.models.database.engine import create_db_engine, get_session
    from flexi.models.database.migrate import run_migrations
    from flexi.services.registry import Services

    run_migrations()
    engine = create_db_engine()
    session = get_session(engine)
    services = Services.build(session)
    services.clock.sweep()
    services.bank_holidays.fill_if_empty()

    handles = Handles(engine=engine, session=session, services=services)
    ctx.obj = handles
    ctx.call_on_close(handles.close)
    return handles


@cli.group()
def holidays() -> None:
    """Look after the bank holiday calendar."""


@holidays.command(name="refresh")
@requires_setup
def holidays_refresh(services: Services) -> int:
    """Fetch the calendar for the configured region from GOV.UK."""
    from flexi.cli import holidays as holidays_cli

    return holidays_cli.run(services)


def run_demo() -> None:
    """Launch against a temporary database holding the sample data.

    The same seed the screenshots and the regression tests use, so what a new
    user is shown, what a reviewer looks at, and what CI compares against are
    all the same working life -- anchored to today here, and to a fixed date
    there, because a committed screenshot cannot move and a demo must.

    Seeded up to today rather than up to `samples.ANCHOR`. That date is in the
    screenshots for good reasons and none of them apply here: the demo opens on
    the real current week, so a fixed anchor meant an empty dashboard and a
    week's deficit for anybody who ran `flexi --demo` after it.
    """
    import tempfile
    from pathlib import Path

    from flexi.app import FlexiApp
    from flexi.models.database.db import Base
    from flexi.models.database.engine import create_db_engine, get_session
    from flexi.services.samples import seed_demo

    # `ignore_cleanup_errors`, because the last thing a demo may do is fail to
    # tidy up after itself. Flexi asks GOV.UK and PyPI from worker threads, and
    # one still finishing as the application closes will have reopened the
    # database -- which Windows then refuses to delete, so quitting the demo
    # ended in a PermissionError traceback rather than a prompt. The file is a
    # throwaway in the system temporary directory either way.
    with tempfile.TemporaryDirectory(
        prefix="flexi-demo-", ignore_cleanup_errors=True
    ) as directory:
        path = Path(directory) / "demo.db"
        engine = create_db_engine(path)
        Base.metadata.create_all(engine)
        session = get_session(engine)
        seed_demo(session, anchor=wallclock.today())
        session.close()
        engine.dispose()
        FlexiApp(db_path=path).run()


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Set Flexi up on this machine.

    On a machine that already has records, this shows what is there and offers
    what can be done about it, including starting again.
    """
    db_path = database_file()

    if is_initialised():
        already_set_up(ctx, db_path)
        return

    from flexi.models.database.migrate import run_migrations

    run_migrations()
    if is_initialised():
        click.secho("Flexi is set up.", fg="green")
        return
    ask_the_questions(ctx, db_path, then_open=False)


def already_set_up(ctx: click.Context, db_path: Path) -> None:
    """Show what is recorded, and do what is chosen about it."""
    from flexi.cli import init as init_cli
    from flexi.cli import ui

    if not ui.interactive():
        # The whole of the headless behaviour, deliberately: report and stop.
        # There is no flag that erases Flexi's records with nobody present.
        click.echo(f"Flexi is set up. Its records are at {db_path}.")
        click.echo("Run `flexi init` from a terminal to change or reset them.")
        return

    contents = init_cli.describe(db_path)
    choice = init_cli.ask(db_path, contents)

    if choice is None:
        return
    if choice is init_cli.Choice.OPEN:
        open_app()
        return
    if choice is init_cli.Choice.SETTINGS:
        open_app(settings=True)
        return

    if not init_cli.confirm_reset(contents):
        ui.abandon("Nothing was erased.")
        return
    erase(db_path)
    ask_the_questions(ctx, db_path, then_open=False)


def open_app(*, settings: bool = False) -> None:
    launch(settings=settings).run()


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

    ``then_open`` is what separates the two ways in. Bare ``flexi`` carries
    straight on into the application once the questions are answered, because
    that is what the person asked for; ``flexi init`` stops and says so.
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

    launch(splash=True).run()

    if not is_initialised():
        click.echo("Setup was not completed.")
        ctx.exit(1)
    if not then_open:
        click.secho(f"Flexi is set up. Its records are at {db_path}.", fg="green")


@cli.group()
def clock() -> None:
    """Clock in or out."""


@clock.command(name="in")
@requires_setup
def clock_in(services: Services) -> int:
    """Clock in to start a work session."""
    from flexi.cli import clock as clock_cli

    return clock_cli.clock_in(services)


@clock.command(name="out")
@requires_setup
def clock_out(services: Services) -> int:
    """Clock out to end the current work session."""
    from flexi.cli import clock as clock_cli

    return clock_cli.clock_out(services)


@cli.command(
    context_settings={"ignore_unknown_options": True},
    short_help="Book or cancel leave in one line.",
)
@click.argument("words", nargs=-1, required=True)
@click.option("--note", default=None, help="A note, required for `other`.")
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@click.option("--dry-run", is_flag=True, help="Show the plan and stop.")
@requires_setup
def leave(
    services: Services,
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

    The plan is shown before anything is written.
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
@as_of_option("Report the balance as at the end of this date. Defaults to today.")
@requires_setup
def balance_show(services: Services, as_of: date | None) -> int:
    """Print the running balance and what it is made of."""
    from flexi.cli import balance as balance_cli

    return balance_cli.show(services, as_of)


@balance.command(name="zero")
@as_of_option("Settle up to and including this date. Defaults to yesterday.")
@click.option("--reason", default=None, help="Why the balance was settled.")
@click.option("--yes", is_flag=True, help="Do not ask.")
@requires_setup
def balance_zero(
    services: Services,
    as_of: date | None,
    reason: str | None,
    *,
    yes: bool,
) -> int:
    """Draw a line under everything up to a date."""
    from flexi.cli import balance as balance_cli

    return balance_cli.zero(services, as_of, reason, assume_yes=yes)


@balance.command(name="log")
@requires_setup
def balance_log(services: Services) -> int:
    """List every correction ever recorded."""
    from flexi.cli import balance as balance_cli

    return balance_cli.log(services)


@balance.command(name="undo")
@click.argument("adjustment_id", type=int)
@requires_setup
def balance_undo(services: Services, adjustment_id: int) -> int:
    """Remove a correction by its id, as listed by `flexi balance log`."""
    from flexi.cli import balance as balance_cli

    return balance_cli.undo(services, adjustment_id)


if __name__ == "__main__":
    cli()
