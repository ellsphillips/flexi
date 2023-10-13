import sys

import click

from flexi import __version__
from flexi.ui import print_welcome
from flexi.utils import print_help_msg


@click.group(no_args_is_help=False, invoke_without_command=True)
@click.version_option(None, "-v", "--version", message=__version__)
def main() -> None:
    """`flexi` entry point."""
    print_welcome()

    if not len(sys.argv) > 1:
        print_help_msg(main)
        return
