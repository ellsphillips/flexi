import pathlib
import sys
import time

import click
import rich

from flexi import __version__
from flexi.constants import HOME_DIR, Clock
from flexi.core import Flexi
from flexi.ui import print_welcome
from flexi.utils import print_help_msg


@click.group(no_args_is_help=False, invoke_without_command=True)
@click.version_option(None, "-v", "--version", message=__version__)
def flexi() -> None:  # pragma: no cover
    """`flexi` entry point."""
    print_welcome()

    if not len(sys.argv) > 1:
        print_help_msg(flexi)
        return


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
def clock(clock: Clock) -> None:
    """Clock in or out."""
    colour = "green" if clock is Clock.IN else "red"
    badge = click.style(f" {clock.name} ", fg="white", bg=colour)
    click.secho(
        f"Successfully clocked {badge} at {time.strftime('%Y-%m-%d %H:%M')}.",
        bold=True,
    )
