import pathlib

import click
import rich

from flexi import __version__
from flexi.constants import HOME_DIR, Clock
from flexi.core import Flexi
from flexi.log.core import load_log
from flexi.log.model import Log
from flexi.log.registrar import JSONRegistrar
from flexi.ui import print_welcome
from flexi.utils import print_help_msg


@click.group(no_args_is_help=False, invoke_without_command=True)
@click.version_option(None, "-v", "--version", message=__version__)
@click.pass_context
def flexi(ctx: click.Context) -> None:  # pragma: no cover
    """`flexi` entry point."""
    print_welcome()

    try:
        ctx.obj = load_log(HOME_DIR / "log.json")
    except FileNotFoundError:
        rich.print(
            "\n".join(
                [
                    "[red b]Error:[/] flexi has not been initialized yet.",
                    "Run [cyan b]flexi init[/] to get started.",
                ]
            )
        )
        return

    if ctx.invoked_subcommand is None:
        print_help_msg(flexi)


@flexi.command()
@click.option(
    "-d",
    "--directory",
    type=pathlib.Path,
    required=False,
    default=HOME_DIR,
    help="Specify flexi's config folder. Defaults to the user's home.",
)
def init(directory: pathlib.Path) -> None:
    """Initialize flexi."""
    f = Flexi()
    f.initialize(directory)
    rich.print(f"Successfully initialized flexi at [b cyan]{directory}[/].")


@flexi.command()
@click.argument(
    "clock",
    type=click.Choice(list(Clock), case_sensitive=False),
    required=False,
)
@click.pass_obj
def clock(log: Log, clock: Clock) -> None:
    """Clock in or out."""
    registrar = JSONRegistrar(HOME_DIR / "log.json", log)

    registrar.record_clock(clock)
