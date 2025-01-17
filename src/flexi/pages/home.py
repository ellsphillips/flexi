from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, Static

from flexi.components.status import Status
from flexi.components.welcome import Welcome


class Home(Static):
    """The home page."""

    DEFAULT_CSS = """
        .home-container {
            height: auto;
            width: 1fr;
            padding: 0 2;

            &.flex-row {
                layout: horizontal;
                width: 1fr;

                .control-panel-group {
                    width: 30%;
                }
                .data-panel-group {
                    width: 70%
                }
            }

            &.flex-col {
                layout: vertical;
            }
        }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs, id="home-page")

        self.arrived = True

        self.STATUS_ISLAND = Status(parent=self)

    def on_layout_change(self, layout: str) -> None:
        layout_container = self.query(".home-container")
        if len(layout_container) > 0:
            layout_container[0].set_classes(f"home-container {layout}")

    def on_mount(self) -> None:
        self.app.watch(self.app, "layout", self.on_layout_change)

    def action_toggle_status(self) -> None:
        self.arrived = not self.arrived
        self.STATUS_ISLAND.rebuild()

    def compose(self) -> ComposeResult:
        with Container(classes="home-container flex-row"):
            with Static(classes="control-panel-group"):
                yield self.STATUS_ISLAND
                yield Label("INSIGHTS")
            with Static(classes="data-panel-group"):
                yield Welcome()
