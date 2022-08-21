from datetime import datetime
from typing import Callable, Dict

from flexi.cli.errors import MissingHomeError
from flexi.io.dot import check_home_initialised

DispatchHandler = Callable[[str], None]


def handle_init_action(value: str) -> None:
    if check_home_initialised():
        return None


def handle_clock_action(value: str) -> None:
    if not check_home_initialised():
        raise MissingHomeError("You must initialise flexi before use")

    now = datetime.now().strftime("%H:%M %d/%m/%Y")
    actions = {
        "in": f"Successfully clocked in at {now}",
        "out": f"Successfully clocked out at {now}",
    }
    print(actions[value])


def handle_status_action(value: str) -> None:
    print(f"Status action requested [{value}]")


DISPATCH_HANDLERS: Dict[str, DispatchHandler] = {
    "init": handle_init_action,
    "clock": handle_clock_action,
    "status": handle_status_action,
}
