from dataclasses import dataclass
from pathlib import Path

from flexi.config.home import create_basic_user_config, create_home_dir


@dataclass
class Flexi:
    """flexi facade."""

    def initialize(self, directory: Path) -> None:
        """Initialize flexi."""
        create_home_dir(directory)
        create_basic_user_config(directory)
