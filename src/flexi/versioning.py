import requests
from packaging import version

import flexi


def get_pypi_version() -> str | None:
    """Fetch the latest version from PyPI."""
    try:
        response = requests.get("https://pypi.org/pypi/flexi/json")
        pypi_version = response.json()["info"]["version"]
        return str(pypi_version)
    except Exception:
        return None


def needs_update() -> bool:
    """Check if the current version needs an update."""
    pypi_version = get_pypi_version()

    if pypi_version is None:
        return False

    return bool(version.parse(pypi_version) > version.parse(flexi.__version__))
