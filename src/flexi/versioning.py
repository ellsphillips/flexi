"""Whether PyPI has a newer Flexi than the one running."""

from __future__ import annotations

import httpx
from packaging.version import InvalidVersion, Version

import flexi

PYPI_URL = "https://pypi.org/pypi/flexi/json"
TIMEOUT_SECONDS = 5.0


def get_pypi_version() -> str | None:
    """The latest published version, or None if PyPI could not be read."""
    try:
        response = httpx.get(PYPI_URL, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return str(response.json()["info"]["version"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def available_update() -> str | None:
    """The published version, when it is newer than this one.

    One request, and one answer that carries the version rather than a bare
    True the caller has to go and fetch again.
    """
    latest = get_pypi_version()
    if latest is None:
        return None
    try:
        newer = Version(latest) > Version(flexi.__version__)
    except InvalidVersion:
        return None
    return latest if newer else None
