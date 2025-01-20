from typing import Any, Literal

from textual.app import ComposeResult
from textual.widgets import Label, Static

from flexi.util.style import load_css


class Badge(Static):
    DEFAULT_CSS = load_css(__file__)

    def __init__(
        self,
        content: str,
        /,
        variant: Literal["primary", "secondary"] = "primary",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.content = content
        self.variant = variant
        super().__init__(content, *args, **kwargs)

    def compose(self) -> ComposeResult:
        yield Label(
            self.content,
            classes=f"badge-{'primary' if self.variant == 'primary' else 'secondary'}",
        )
