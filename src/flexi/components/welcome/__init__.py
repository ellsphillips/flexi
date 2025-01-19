from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import MarkdownViewer, Static

from flexi.locations import STATIC_DIRECTORY
from flexi.util.style import load_css


class Welcome(Static):
    """New user info."""

    DEFAULT_CSS = load_css(__file__)
    can_focus = True

    def __init__(self) -> None:
        super().__init__(classes="island")
        file_path = STATIC_DIRECTORY / "welcome.md"
        with open(file_path, "r") as file:
            self.welcome_text = file.read()

    def compose(self) -> ComposeResult:
        with Container(id="welcome-container"):
            with Static(classes="text-container"):
                yield MarkdownViewer(self.welcome_text, show_table_of_contents=False)
