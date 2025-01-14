from importlib.metadata import metadata

from textual.app import App as TextualApp
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Label

from flexi.pages.home import Home
from flexi.theme import THEME
from flexi.user import get_user_host_string


class App(TextualApp[None]):
    CSS_PATH = "index.scss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()

        # Get package metadata directly
        meta = metadata("flexi")
        self.project_info = {"name": meta["Name"], "version": meta["Version"]}

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "flexi"

    def compose(self) -> ComposeResult:
        version = self.project_info["version"]
        user_host = get_user_host_string()
        with Container(classes="header"):
            yield Label(f"↪ {self.project_info['name']}", classes="title")
            yield Label(version, classes="version")
            yield Label(user_host, classes="user")
        yield Home(classes="content")
        yield Footer()
