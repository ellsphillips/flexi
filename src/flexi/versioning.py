from __future__ import annotations

import httpx
from packaging import version

import flexi


def get_pypi_version() -> str | None:
    """Fetch the latest version from PyPI."""
    try:
        response = httpx.get("https://pypi.org/pypi/flexi/json", timeout=5.0)
        response.raise_for_status()
        pypi_version = response.json()["info"]["version"]
        return str(pypi_version)
    except Exception:  # noqa: BLE001
        return None


def needs_update() -> bool:
    """Check if the current version needs an update."""
    pypi_version = get_pypi_version()

    if pypi_version is None:
        return False

    return bool(version.parse(pypi_version) > version.parse(flexi.__version__))
