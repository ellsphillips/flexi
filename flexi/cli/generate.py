from argparse import ArgumentParser

from flexi.cli.flags import (
    attach_clock_flag,
    attach_init_flag,
    attach_status_flag,
    attach_version_flag,
)


def generate_parser() -> ArgumentParser:

    parser = ArgumentParser(description="Command line interface tool for flexi.")

    attach_version_flag(parser)
    attach_init_flag(parser)
    attach_clock_flag(parser)
    attach_status_flag(parser)

    return parser
