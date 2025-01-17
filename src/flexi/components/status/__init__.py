from typing import Any, Protocol

from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Button, Static

from flexi.util.style import load_css


class Parent(Protocol):
    """Interface for this component's parent."""

    arrived: bool

    def action_toggle_status(self) -> None: ...


class Status(Static):
    """The status island."""

    DEFAULT_CSS = load_css(__file__)

    def __init__(self, parent: Parent, *args: Any, **kwargs: Any) -> None:
        super().__init__(
            *args, **kwargs, id="incomemode-container", classes="module-container"
        )
        super().__setattr__("border_title", "Arrive / Depart")
        super().__setattr__("border_subtitle", "/")
        self.page_parent = parent

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        arrive_button: Button | Widget = self.query_one("#arrive-button")
        depart_button: Button | Widget = self.query_one("#depart-button")
        arrived = self.page_parent.arrived
        arrive_button.classes = "selected" if not arrived else ""
        depart_button.classes = "selected" if arrived else ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.page_parent.action_toggle_status()

    def compose(self) -> ComposeResult:
        with Container(id="status-container", classes="island"):
            yield Button("Arrive", id="arrive-button")
            yield Button("Depart", id="depart-button")
