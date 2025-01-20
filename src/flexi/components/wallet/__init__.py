from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, Rule, Static

from flexi.util.style import load_css


def statistic(key: str, value: str) -> ComposeResult:
    yield Label(key).set_styles("padding-top: 1;")
    yield Label(value, classes="highlight")


class Wallet(Static):
    """The wallet island."""

    DEFAULT_CSS = load_css(__file__)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs, classes="island")

    def compose(self) -> ComposeResult:
        with Container(id="wallet-container"):
            yield Label("Wallet overview")
            yield Rule()
            yield from statistic("Time balance", "14:48")
            yield from statistic("Annual leave remaining", "9 / 30")
