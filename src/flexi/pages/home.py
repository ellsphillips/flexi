from typing import Any

from textual.app import ComposeResult
from textual.widgets import Label, Static

from flexi.components.badge import Badge
from flexi.components.welcome import Welcome


class Home(Static):
    """The home page."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs, id="home-page")

    def compose(self) -> ComposeResult:
        with Static(classes="home-modules-container v"):
            with Static(classes="left"):
                yield Label("Placeholder content")
            with Static(classes="right"):
                yield Welcome()
                yield Badge("New", variant="primary")
                yield Badge("Beta", variant="secondary")
