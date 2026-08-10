import functools
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import click

import flexi
from flexi import wallclock
from flexi.app import App
from flexi.domain.format import long_date, stamp
from flexi.locations import database_file
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services.clock import ClockService
from flexi.services.setup import is_initialised
from flexi.services.startup import run_startup_cleanup


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
    run_migrations()
    if not is_initialised():
        _ask_the_questions(ctx, database_file())
        return
    App().run()


NOT_INITIALISED = (
    "Flexi is not set up on this machine yet.\n"
    "Run `flexi init` to choose your leave year, hours and bank holidays."
)


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


def _open_database(ctx: click.Context) -> None:
    """Migrate, connect, sweep, and stash on the context."""
    run_migrations()
    engine = create_db_engine()
    session = get_session(engine)
    run_startup_cleanup(session)
    ctx.ensure_object(dict)
    ctx.obj["engine"] = engine
    ctx.obj["session"] = session


def _run_demo() -> None:
    """Launch against a temporary database holding the sample data.

    The same seed the screenshots and the regression tests use, so what a new
    user is shown, what a reviewer looks at, and what CI compares against are all
    the same six weeks.
    """
    import tempfile
    from pathlib import Path

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
    run_migrations()
    app = App()
    app.open_settings = settings
    app.run()


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

    app = App()
    app.show_splash = True
    app.run()

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
    session = ctx.obj["session"]
    svc = ClockService(session)
    result = svc.clock_in()

    if result.success:
        click.secho(result.message, fg="green")
    else:
        click.secho(result.message, fg="red")
        ctx.exit(1)

    session.close()
    ctx.obj["engine"].dispose()


@clock.command(name="out")
@requires_setup
@click.pass_context
def clock_out(ctx: click.Context) -> None:
    """Clock out to end the current work session."""
    session = ctx.obj["session"]
    svc = ClockService(session)
    result = svc.clock_out()

    if result.success:
        click.secho(result.message, fg="green")
    else:
        click.secho(result.message, fg="red")
        ctx.exit(1)

    session.close()
    ctx.obj["engine"].dispose()


@cli.command(
    context_settings={"ignore_unknown_options": True},
    short_help="Book or cancel leave in one line.",
)
@click.argument("words", nargs=-1, required=True)
@click.option("--note", default=None, help="A note, required for `other`.")
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@click.option("--dry-run", is_flag=True, help="Show the plan and stop.")
@click.pass_context
@requires_setup
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
    from flexi.services.registry import Services

    services = Services.build(ctx.obj["session"])
    code = leave_cli.run(
        services,
        words,
        note=note,
        assume_yes=yes,
        dry_run=dry_run,
        today=wallclock.today(),
    )
    _close(ctx)
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
    from flexi.domain.format import delta, hm
    from flexi.services.registry import Services

    services = Services.build(ctx.obj["session"])
    today = as_of.date() if as_of is not None else wallclock.today()
    start, _ = services.absence.leave_year_bounds(today)
    summary = services.ledger.balance(today)

    click.echo(
        f"leave year   {stamp(start, '%-d %b %Y')} → {stamp(today, '%-d %b %Y')}"
    )
    click.echo(f"worked       {hm(summary.worked)}")
    click.echo(f"expected     {hm(summary.expected)}")
    if summary.toil_taken:
        click.echo(f"toil taken   {hm(summary.toil_taken)}")
    if summary.adjustment:
        click.echo(f"adjusted     {delta(summary.adjustment)}")
    click.secho(f"balance      {delta(summary.delta)}", bold=True)
    _close(ctx)


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
    """Draw a line under everything up to a date.

    Records one signed adjustment rather than deleting anything: the clock
    events that produced the balance stay exactly where they are, and the line
    can be removed again with `flexi balance undo`.
    """
    from datetime import timedelta

    from flexi.domain.format import delta
    from flexi.services.adjustments import OPENING_BALANCE
    from flexi.services.registry import Services

    services = Services.build(ctx.obj["session"])
    when = as_of.date() if as_of is not None else wallclock.today() - timedelta(days=1)
    standing = services.ledger.balance(when).delta

    click.echo(f"balance as at {long_date(when)} is {delta(standing)}")
    if not yes and not click.confirm("Settle it to zero?", default=True):
        click.echo("Left alone.")
        _close(ctx)
        return

    result = services.zero_balance(when, reason=reason or OPENING_BALANCE)
    click.secho(result.message, fg="green" if result.success else "red")
    if result.success:
        click.echo(
            f"balance now   {delta(services.ledger.balance(wallclock.today()).delta)}"
        )
    _close(ctx)
    if not result.success:
        ctx.exit(1)


@balance.command(name="log")
@requires_setup
@click.pass_context
def balance_log(ctx: click.Context) -> None:
    """List every correction ever recorded."""
    from datetime import timedelta

    from flexi.domain.format import delta
    from flexi.services.registry import Services

    services = Services.build(ctx.obj["session"])
    rows = services.adjustments.all()
    if not rows:
        click.echo("No adjustments.")
    for row in rows:
        click.echo(
            f"{row.id:>4}  {row.date:%Y-%m-%d}  "
            f"{delta(timedelta(minutes=row.minutes)):>9}  {row.reason}"
        )
    _close(ctx)


@balance.command(name="undo")
@click.argument("adjustment_id", type=int)
@requires_setup
@click.pass_context
def balance_undo(ctx: click.Context, adjustment_id: int) -> None:
    """Remove a correction by its id, as listed by `flexi balance log`."""
    from flexi.services.registry import Services

    services = Services.build(ctx.obj["session"])
    result = services.adjustments.remove(adjustment_id)
    click.secho(result.message, fg="green" if result.success else "red")
    _close(ctx)
    if not result.success:
        ctx.exit(1)


def _close(ctx: click.Context) -> None:
    ctx.obj["session"].close()
    ctx.obj["engine"].dispose()


if __name__ == "__main__":
    cli()
