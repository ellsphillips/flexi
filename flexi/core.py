from dataclasses import dataclass

from flexi.config.home import create_basic_user_config, create_home_dir


@dataclass
class Flexi:
    """flexi facade."""

    def initialize(self) -> None:
        """Initialize flexi."""
        create_home_dir()
        create_basic_user_config()
