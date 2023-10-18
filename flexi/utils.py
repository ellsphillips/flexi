import click


def print_help_msg(command: click.Command) -> None:
    """Prints the help message for a given command."""
    with click.Context(command) as ctx:
        click.echo(command.get_help(ctx))
