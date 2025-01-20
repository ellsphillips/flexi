from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

from flexi.components.calendar import Calendar
from flexi.components.status import Status
from flexi.components.wallet import Wallet
from flexi.components.welcome import Welcome
from flexi.constants import StatusOption


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
                    width: 28;
                }
                .data-panel-group {
                    width: 1fr;
                }
            }

            &.flex-col {
                layout: vertical;
            }
        }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs, id="home-page")

        self.status = StatusOption.DEPART

        self.STATUS_ISLAND = Status(parent=self)
        self.CALENDAR_ISLAND = Calendar(parent=self)
        self.WALLET_ISLAND = Wallet()

    def rebuild(self) -> None:
        self.STATUS_ISLAND.rebuild()
        self.CALENDAR_ISLAND.rebuild()

    def on_layout_change(self, layout: str) -> None:
        layout_container = self.query(".home-container")
        if len(layout_container) > 0:
            layout_container[0].set_classes(f"home-container {layout}")

    def on_mount(self) -> None:
        self.app.watch(self.app, "layout", self.on_layout_change)

    def action_set_status(self, status: StatusOption) -> None:
        if status is self.status:
            return  # filter redundancy

        self.status = status
        self.rebuild()

        self.notify(
            f"Status: {self.status}",
        )

    def compose(self) -> ComposeResult:
        with Container(classes="home-container flex-row"):
            with Static(classes="control-panel-group"):
                yield self.STATUS_ISLAND
                yield self.CALENDAR_ISLAND
                yield self.WALLET_ISLAND
            with Static(classes="data-panel-group"):
                yield Welcome()
