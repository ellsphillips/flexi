from typing import Any, Protocol

from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Button, Static

from flexi.constants import StatusOption
from flexi.util.style import load_css


class Parent(Protocol):
    """Interface for this component's parent."""

    status: StatusOption

    def action_set_status(self, status: StatusOption) -> None: ...


class Status(Static):
    """The status island."""

    DEFAULT_CSS = load_css(__file__)

    def __init__(self, parent: Parent, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs, classes="island")
        super().__setattr__("border_title", "Status")
        super().__setattr__("border_subtitle", "/")
        self.page_parent = parent

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        buttons: dict[StatusOption, Widget] = {
            StatusOption.ARRIVE: self.query_one("#arrive-button"),
            StatusOption.DEPART: self.query_one("#depart-button"),
        }

        for status, button in buttons.items():
            button.classes = "selected" if self.page_parent.status is status else ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if (name := event.button.name) is not None:
            state = StatusOption.from_str(name)
            self.page_parent.action_set_status(state)

    def compose(self) -> ComposeResult:
        with Container(id="status-container"):
            yield Button("Arrive", id="arrive-button", name="arrive")
            yield Button("Depart", id="depart-button", name="depart")
