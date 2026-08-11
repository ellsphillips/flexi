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
from typing import TYPE_CHECKING

import click

import flexi
from flexi import wallclock
from flexi.locations import database_file
from flexi.services.setup import is_initialised

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from sqlalchemy import Engine
    from sqlalchemy.orm import Session

    from flexi.app import App
    from flexi.services.registry import Services


@click.group(invoke_without_command=True)
@click.version_option(None, "-v", "--version", message=flexi.__version__)
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
        _run_demo()
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
        _ask_the_questions(ctx, database_file())
        return
    _launch().run()


NOT_INITIALISED = (
    "Flexi is not set up on this machine yet.\n"
    "Run `flexi init` to choose your leave year, hours and bank holidays."
)


@dataclass(frozen=True, slots=True)
class Handles:
    """An open database, and the means to let go of it.

    Reached through :func:`handles_of` rather than out of ``ctx.obj``, which
    Click types as ``Any`` -- thirteen accesses that ``mypy --strict`` could not
    check, and a rename away from failing at runtime.
    """

    engine: Engine
    session: Session
    services: Services

    def close(self) -> None:
        self.session.close()
        self.engine.dispose()


def handles_of(ctx: click.Context) -> Handles:
    """The database this command opened. Guaranteed by `requires_setup`."""
    handles = ctx.find_object(Handles)
    if handles is None:  # pragma: no cover - requires_setup opens it first
        msg = "no database is open on this context"
        raise RuntimeError(msg)
    return handles


def requires_setup(command: Callable[..., None]) -> Callable[..., None]:
    """Refuse before setup; migrate and open a session after it.

    Applied per command rather than to the group, so `flexi init` and every
    `--help` remain reachable on a machine with no database.
    """

    @functools.wraps(command)
    @click.pass_context
    def guarded(ctx: click.Context, /, *args: object, **kwargs: object) -> None:
        if not is_initialised():
            click.secho(NOT_INITIALISED, fg="yellow", err=True)
            ctx.exit(1)
        _open_database(ctx)
        ctx.invoke(command, *args, **kwargs)

    return guarded


def _launch(*, settings: bool = False, splash: bool = False) -> App:
    """Every way into the application goes through here.

    ``App.__init__`` builds an engine, opens a session and reads the settings
    row, so opening it against a database with no tables raises before a single
    screen is drawn. Leaving each caller to migrate first meant the invariant
    lived everywhere except where it was needed, and the reset path -- which
    deletes the database and then asks the five questions -- duly forgot.

    ``run_migrations`` returns as soon as it finds the schema already at head,
    so calling it on every path costs one revision check and takes no extra
    backup. That is a cheap price for the guarantee.
    """
    from flexi.app import App
    from flexi.models.database.migrate import run_migrations

    run_migrations()
    app = App()
    app.open_settings = settings
    app.show_splash = splash
    return app


def _open_database(ctx: click.Context) -> Handles:
    """Migrate, connect, sweep, and hand back an open database.

    Closing is registered on the context rather than written at the end of each
    command. `ctx.exit` raises, so every hand-written `session.close()` after a
    failure was unreachable -- which is to say the session and the engine leaked
    on exactly the paths where something had already gone wrong.
    """
    from flexi.models.database.app import create_db_engine, get_session
    from flexi.models.database.migrate import run_migrations
    from flexi.services.registry import Services
    from flexi.services.startup import run_startup_cleanup

    run_migrations()
    engine = create_db_engine()
    session = get_session(engine)
    services = Services.build(session)
    run_startup_cleanup(
        session, services.clock, services.settings.get_auto_close_time()
    )
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
@click.pass_context
def holidays_refresh(ctx: click.Context) -> None:
    """Fetch the calendar for the configured region from GOV.UK."""
    from flexi.cli import holidays as holidays_cli

    code = holidays_cli.run(handles_of(ctx).services)
    ctx.exit(code)


def _run_demo() -> None:
    """Launch against a temporary database holding the sample data.

    The same seed the screenshots and the regression tests use, so what a new
    user is shown, what a reviewer looks at, and what CI compares against are all
    the same six weeks.
    """
    import tempfile
    from pathlib import Path

    from flexi.app import App
    from flexi.models.database.app import create_db_engine, get_session
    from flexi.models.database.db import Base
    from flexi.services.samples import seed_demo

    with tempfile.TemporaryDirectory(prefix="flexi-demo-") as directory:
        path = Path(directory) / "demo.db"
        engine = create_db_engine(path)
        Base.metadata.create_all(engine)
        session = get_session(engine)
        seed_demo(session)
        session.close()
        engine.dispose()
        App(db_path=path).run()


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Set Flexi up on this machine.

    On a machine that already has records, this shows what is there and offers
    what can be done about it, including starting again.
    """
    db_path = database_file()

    if is_initialised():
        _already_set_up(ctx, db_path)
        return

    from flexi.models.database.migrate import run_migrations

    run_migrations()
    if is_initialised():
        click.secho("Flexi is set up.", fg="green")
        return
    _ask_the_questions(ctx, db_path, then_open=False)


def _already_set_up(ctx: click.Context, db_path: Path) -> None:
    """Show what is recorded, and do what is chosen about it."""
    from flexi.cli import init as init_cli
    from flexi.cli import ui

    if not init_cli.interactive():
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
        _open_app()
        return
    if choice is init_cli.Choice.SETTINGS:
        _open_app(settings=True)
        return

    if not init_cli.confirm_reset(contents):
        ui.abandon("Nothing was erased.")
        return
    _erase(db_path)
    _ask_the_questions(ctx, db_path, then_open=False)


def _open_app(*, settings: bool = False) -> None:
    _launch(settings=settings).run()


def _erase(db_path: Path) -> None:
    """Snapshot, remove the records, and forget that this path was ever set up."""
    from flexi.cli import init as init_cli
    from flexi.services import setup as setup_service

    taken = init_cli.reset(db_path)
    setup_service.forget(db_path)
    if taken is not None:
        init_cli.settled(f"Erased. Snapshot kept at {taken}")


def _ask_the_questions(
    ctx: click.Context, db_path: Path, *, then_open: bool = True
) -> None:
    """Open the setup form, which is a full screen and needs a terminal.

    ``then_open`` is what separates the two ways in. Bare ``flexi`` carries
    straight on into the application once the questions are answered, because
    that is what the person asked for; ``flexi init`` stops and says so.
    """
    from flexi.cli import init as init_cli

    if not init_cli.interactive():
        click.secho(
            f"The database is ready at {db_path}, but setup needs answering.\n"
            "Run `flexi init` from a terminal to finish.",
            fg="yellow",
            err=True,
        )
        ctx.exit(1)

    _launch(splash=True).run()

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
@click.pass_context
def clock_in(ctx: click.Context) -> None:
    """Clock in to start a work session."""
    from flexi.cli import clock as clock_cli

    ctx.exit(clock_cli.clock_in(handles_of(ctx).services))


@clock.command(name="out")
@requires_setup
@click.pass_context
def clock_out(ctx: click.Context) -> None:
    """Clock out to end the current work session."""
    from flexi.cli import clock as clock_cli

    ctx.exit(clock_cli.clock_out(handles_of(ctx).services))


@cli.command(
    context_settings={"ignore_unknown_options": True},
    short_help="Book or cancel leave in one line.",
)
@click.argument("words", nargs=-1, required=True)
@click.option("--note", default=None, help="A note, required for `other`.")
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@click.option("--dry-run", is_flag=True, help="Show the plan and stop.")
@requires_setup
@click.pass_context
def leave(
    ctx: click.Context,
    words: tuple[str, ...],
    note: str | None,
    *,
    yes: bool,
    dry_run: bool,
) -> None:
    r"""Book or cancel leave without opening the application.

    \b
    flexi leave annual friday
    flexi leave annual monday to friday
    flexi leave sick today pm
    flexi leave toil 12 jun
    flexi leave cancel next monday

    The plan is shown before anything is written.
    """
    from flexi.cli import leave as leave_cli

    services = handles_of(ctx).services
    code = leave_cli.run(
        services,
        words,
        note=note,
        assume_yes=yes,
        dry_run=dry_run,
        today=wallclock.today(),
    )
    ctx.exit(code)


@cli.group()
def balance() -> None:
    """Read and correct the flexi balance."""


@balance.command(name="show")
@click.option(
    "--as-of",
    "as_of",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Report the balance as at the end of this date. Defaults to today.",
)
@requires_setup
@click.pass_context
def balance_show(ctx: click.Context, as_of: datetime | None) -> None:
    """Print the running balance and what it is made of."""
    from flexi.cli import balance as balance_cli

    when = as_of.date() if as_of is not None else None
    ctx.exit(balance_cli.show(handles_of(ctx).services, when))


@balance.command(name="zero")
@click.option(
    "--as-of",
    "as_of",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Settle up to and including this date. Defaults to yesterday.",
)
@click.option("--reason", default=None, help="Why the balance was settled.")
@click.option("--yes", is_flag=True, help="Do not ask.")
@requires_setup
@click.pass_context
def balance_zero(
    ctx: click.Context,
    as_of: datetime | None,
    reason: str | None,
    *,
    yes: bool,
) -> None:
    """Draw a line under everything up to a date."""
    from flexi.cli import balance as balance_cli

    when = as_of.date() if as_of is not None else None
    ctx.exit(balance_cli.zero(handles_of(ctx).services, when, reason, assume_yes=yes))


@balance.command(name="log")
@requires_setup
@click.pass_context
def balance_log(ctx: click.Context) -> None:
    """List every correction ever recorded."""
    from flexi.cli import balance as balance_cli

    ctx.exit(balance_cli.log(handles_of(ctx).services))


@balance.command(name="undo")
@click.argument("adjustment_id", type=int)
@requires_setup
@click.pass_context
def balance_undo(ctx: click.Context, adjustment_id: int) -> None:
    """Remove a correction by its id, as listed by `flexi balance log`."""
    from flexi.cli import balance as balance_cli

    ctx.exit(balance_cli.undo(handles_of(ctx).services, adjustment_id))


if __name__ == "__main__":
    cli()
