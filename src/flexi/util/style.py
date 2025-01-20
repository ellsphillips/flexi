from pathlib import Path
from typing import Annotated


def load_css(here: Annotated[str, "should always be __file__"]) -> str:
    stylesheet = Path(here).parent.glob(r"*css")

    if not stylesheet:
        raise ValueError("Only CSS files are supported.")

    return (Path(here).parent / "style.scss").read_text()
