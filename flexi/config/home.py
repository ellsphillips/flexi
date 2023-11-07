from dataclasses import asdict
from pathlib import Path

import rich
import toml

from flexi.config.core import DEFAULT_CONFIG


def create_home_dir(directory: Path) -> None:
    """Create the flexi home directory."""
    directory.mkdir(exist_ok=True)

    with Path.open(directory / "log.json", "w") as f:
        f.write("[]")


def create_basic_user_config(home: Path) -> None:
    """Create a basic user config file."""
    if Path.exists(home / "flexi.toml"):
        rich.print("[green]Re-inialising flexi...[/]")

    with Path.open(home / "flexi.toml", "w") as f:
        toml.dump({"user": asdict(DEFAULT_CONFIG)}, f)
