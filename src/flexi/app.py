from textual.app import App as TextualApp
from textual.app import ComposeResult
from textual.widgets import Footer

from flexi.components.header import Header
from flexi.pages.home import Home
from flexi.theme import THEME


class App(TextualApp[None]):
    CSS_PATH = "index.scss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "flexi"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Home(classes="content")
        yield Footer()
