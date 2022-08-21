from argparse import ArgumentParser

import flexi
from flexi.constants import DEFAULT_CONFIG_FILE_NAME


def attach_version_flag(parser: ArgumentParser) -> None:
    """Attaches the --version flag to the given parser"""
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"flexi version {flexi.__version__}",
    )


def attach_file_flag(parser: ArgumentParser) -> None:
    """Attaches the '--file' flag to the given parser"""
    parser.add_argument(
        "-f",
        "--file",
        dest="file",
        default=DEFAULT_CONFIG_FILE_NAME,
        help=f"config file name, defaults to '{DEFAULT_CONFIG_FILE_NAME}'",
    )


def attach_init_flag(parser: ArgumentParser) -> None:
    """Attaches the 'init' flag to the given parser"""
    parser.add_argument(
        "init",
        nargs="?",
        default=None,
        type=lambda s: "INITIALISE" if s else None,
        help="Initialise your flexi record",
    )


def attach_clock_flag(parser: ArgumentParser) -> None:
    """Attaches the '--clock' flag to the given parser"""
    parser.add_argument(
        "-c",
        "--clock",
        metavar="[in, out]",
        action="store",
        help="Are you coming or going?",
    )


def attach_status_flag(parser: ArgumentParser) -> None:
    """Attaches the --status flag to the given parser"""
    parser.add_argument(
        "-s",
        "--status",
        action="store_true",
        default=None,
        help="Overview of current flexi record",
    )
