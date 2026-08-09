from datetime import datetime

import click

import flexi
from flexi import wallclock
from flexi.app import App
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services.clock import ClockService
from flexi.services.startup import run_startup_cleanup


@click.group(invoke_without_command=True)
@click.version_option(None, "-v", "--version", message=flexi.__version__)
@click.option(
    "--demo",
    is_flag=True,
    help="Run against a throwaway database seeded with six weeks of a working life.",
)
@click.pass_context
def cli(ctx: click.Context, demo: bool = False) -> None:
    """Flexi CLI."""
    if demo:
        _run_demo()
        return

    run_migrations()

    ctx.ensure_object(dict)

    if ctx.invoked_subcommand is not None:
        engine = create_db_engine()
        session = get_session(engine)
        run_startup_cleanup(session)
        ctx.obj["engine"] = engine
        ctx.obj["session"] = session
        return

    app = App()
    app.run()


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


@cli.group()
def clock() -> None:
    """Clock in or out."""


@clock.command(name="in")
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
@click.pass_context
def balance_show(ctx: click.Context, as_of: datetime | None) -> None:
    """Print the running balance and what it is made of."""
    from flexi.domain.format import delta, hm
    from flexi.services.registry import Services

    services = Services.build(ctx.obj["session"])
    today = as_of.date() if as_of is not None else wallclock.today()
    start, _ = services.absence.leave_year_bounds(today)
    summary = services.ledger.balance(today)

    click.echo(f"leave year   {start:%-d %b %Y} → {today:%-d %b %Y}")
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
@click.pass_context
def balance_zero(
    ctx: click.Context, as_of: datetime | None, reason: str | None, yes: bool
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

    click.echo(f"balance as at {when:%a %-d %b %Y} is {delta(standing)}")
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
