from typing import Any, Dict

import tomlkit

from flexi.constants import DEFAULT_CONFIG_FILE_NAME


def read_config_file(file_name: str = DEFAULT_CONFIG_FILE_NAME) -> Dict[str, Any]:
    with open(file_name, "r") as raw_config:
        config = raw_config.read()

    return tomlkit.parse(config)
