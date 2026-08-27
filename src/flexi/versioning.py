"""Whether PyPI has a newer Flexi than the one running."""

from __future__ import annotations

import httpx
from packaging.version import InvalidVersion, Version

import flexi

__all__ = ("PYPI_URL", "TIMEOUT_SECONDS", "available_update", "get_pypi_version")

PYPI_URL = "https://pypi.org/pypi/flexi/json"
TIMEOUT_SECONDS = 5.0


def get_pypi_version() -> str | None:
    """The latest published version, or None if PyPI could not be read."""
    # Through a `Client`, like the bank-holiday fetch, because that is the one
    # form the suite can intercept. `httpx.get` builds a client of its own and
    # goes straight to `Client.request`, so patching `httpx.Client.get` -- the
    # seam every other outbound call in this project goes through -- did not
    # catch this one, and the suite needed a second fixture to stub the
    # function instead.
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            response = client.get(PYPI_URL)
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
