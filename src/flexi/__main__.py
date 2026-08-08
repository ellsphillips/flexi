import click

import flexi
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


if __name__ == "__main__":
    cli()
