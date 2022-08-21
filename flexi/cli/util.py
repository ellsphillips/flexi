import os
from typing import Any


def highlight(text: Any, bold: bool = False) -> str:
    return ("\033[1m" if bold else "") + f"\033[96m{text}\033[0m"


def welcome() -> None:
    size = os.get_terminal_size()
    print(highlight(f"{' flexi ':~^{size.columns}}", bold=True), " ")
