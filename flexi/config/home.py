from dataclasses import asdict
from pathlib import Path

import toml
from dotenv import dotenv_values

from flexi.config.core import DEFAULT_CONFIG


def flexi_env() -> dict[str, str]:
    """Load the .env file."""
    env = dotenv_values(".env")
    return {k: v for k, v in env.items() if v is not None}


def find_home() -> Path:
    """Find the flexi home directory."""
    env = flexi_env().get("HOME")
    return Path(env) if env is not None else Path.home() / ".flexi/"


def create_home_dir() -> None:
    """Create the flexi home directory."""
    HOME_DIR.mkdir(exist_ok=True)


def create_basic_user_config() -> None:
    """Create a basic user config file."""
    with Path.open(HOME_DIR / "flexi.toml", "w") as f:
        toml.dump({"user": asdict(DEFAULT_CONFIG)}, f)


HOME_DIR = find_home()
