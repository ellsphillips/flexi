import click

import flexi


def welcome() -> None:
    """Print the flexi welcome title."""
    badge = click.style(" flexi ", fg="white", bg="cyan")
    strapline = click.style(flexi.__doc__, fg="bright_black")
    click.clear()
    click.secho(f"{badge} v{flexi.__version__}\n{strapline}\n", bold=True)
