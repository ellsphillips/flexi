from importlib.metadata import metadata

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, Static

from flexi.user import get_user_host_string
from flexi.util.style import load_css


class Header(Static):
    DEFAULT_CSS = load_css(__file__)

    def __init__(self) -> None:
        super().__init__()

        meta = metadata("flexi")
        self.project_info = {"name": meta["Name"], "version": meta["Version"]}

    def compose(self) -> ComposeResult:
        version = self.project_info["version"]
        user_host = get_user_host_string()
        with Container(classes="header"):
            yield Label(f"↪ {self.project_info['name']}", classes="title")
            yield Label(version, classes="version")
            yield Label(user_host, classes="user")
