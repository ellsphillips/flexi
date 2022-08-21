import json
from typing import Any, List, Tuple

from flexi.cli.handlers.core import DISPATCH_HANDLERS
from flexi.cli.util import welcome
from flexi.config.core import read_config_file

from .generate import generate_parser

ActionList = List[Tuple[str, Any]]


def read_cli_args() -> ActionList:
    parser = generate_parser()

    args = parser.parse_args()

    return [*args._get_args(), *args._get_kwargs()]


def dispatcher(*args: Any, **kwargs: Any) -> None:
    """
    Main CLI method for flexi. Command line actions are received and dispatched to
    the corresponding method.
    """
    cfg = read_config_file()

    print(json.dumps(cfg, indent=4))

    welcome()
    actions: ActionList = read_cli_args()

    for command, value in actions:
        if value is not None:
            DISPATCH_HANDLERS[command](value)
