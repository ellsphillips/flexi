"""Whether PyPI has a newer Flexi than the one running."""

from __future__ import annotations

from packaging.version import InvalidVersion, Version

import flexi
from flexi.network import fetch_json

__all__ = (
    "PYPI_URL",
    "TIMEOUT_SECONDS",
    "UPGRADE_HINT",
    "available_update",
    "get_pypi_version",
)

PYPI_URL = "https://pypi.org/pypi/flexi/json"
TIMEOUT_SECONDS = 5.0
_FETCH_BUDGET = 2 * TIMEOUT_SECONDS
UPGRADE_HINT = "Upgrade with the tool you installed it with, e.g. uv tool upgrade flexi"
"""An example command: which of uv, pipx or pip installed Flexi is unknowable."""


def get_pypi_version() -> str | None:
    """The latest published version, or None if PyPI could not be read."""
    payload = fetch_json(PYPI_URL, timeout=TIMEOUT_SECONDS, budget=_FETCH_BUDGET)
    if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
        return None
    version: object = payload["info"].get("version")
    return version if isinstance(version, str) else None


def available_update() -> str | None:
    """The published version, when it is newer than this one."""
    latest = get_pypi_version()
    if latest is None:
        return None
    try:
        published = Version(latest)
    except InvalidVersion:
        return None
    # The canonical form, so whitespace around a published version cannot reach
    # a toast or the header stamp.
    return str(published) if published > Version(flexi.__version__) else None
