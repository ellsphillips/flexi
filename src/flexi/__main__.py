import click

from flexi import __version__
from flexi.ui import print_welcome


@click.command()
@click.version_option(version=__version__)
def main() -> None:
    """`flexi` entry point."""
    print_welcome()
