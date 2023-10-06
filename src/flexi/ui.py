import click

import flexi


def print_welcome() -> None:
    """Prints the flexi welcome header.

    Args:
        None

    Examples:
        >>> print_welcome()
         flexi  v0.0.1
        Manage your working hours, flexibly.
        <BLANKLINE>
    """
    badge = click.style(" flexi ", fg="white", bg="cyan")
    strapline = click.style(flexi.__doc__, fg="bright_black")
    click.secho(f"{badge} v{flexi.__version__}\n{strapline}\n", bold=True)
