import datetime
import time

import click

from flexi.constants import Clock


def print_successful_clock_message(clock: Clock) -> None:
    """Print a clock message."""
    colour = "green" if clock is Clock.IN else "red"
    badge = click.style(f" {clock.name} ", fg="white", bg=colour)
    click.secho(
        f"Successfully clocked {badge} at {time.strftime('%Y-%m-%d %H:%M')}.",
        bold=True,
    )


def print_help_msg(command: click.Command) -> None:
    """Prints the help message for a given command."""
    with click.Context(command) as ctx:
        click.echo(command.get_help(ctx))


def get_current_time() -> str:
    """Get the current time."""
    return datetime.datetime.now().strftime("%H:%M")  # noqa: DTZ005
