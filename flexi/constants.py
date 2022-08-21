from enum import Enum, auto

HOME_FOLDER = "./.flexi"

DEFAULT_CONFIG_FILE_NAME = "examples/flexi.toml"


class Actions(Enum):
    ARRIVE = auto()
    DEPART = auto()
