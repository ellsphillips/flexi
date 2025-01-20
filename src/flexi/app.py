from textual import events
from textual.app import App as TextualApp
from textual.app import ComposeResult
from textual.geometry import Size
from textual.reactive import Reactive, reactive
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

    layout: Reactive[str] = reactive("flex-col")

    def __init__(self) -> None:
        super().__init__()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "flexi"

    def on_resize(self, event: events.Resize) -> None:
        console_size: Size = event.size
        aspect_ratio = (console_size.width / 2) / console_size.height
        self.layout = "flex-col" if aspect_ratio < 1 else "flex-row"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Home()
        yield Footer()
