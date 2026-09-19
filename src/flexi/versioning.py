"""Whether PyPI has a newer Flexi than the one running."""

from __future__ import annotations

import httpx
from packaging.version import InvalidVersion, Version

import flexi

__all__ = (
    "PYPI_URL",
    "TIMEOUT_SECONDS",
    "UPGRADE_HINT",
    "available_update",
    "get_pypi_version",
)

PYPI_URL = "https://pypi.org/pypi/flexi/json"
TIMEOUT_SECONDS = 5.0
UPGRADE_HINT = "Upgrade with the tool you installed it with, e.g. uv tool upgrade flexi"
"""An example command: which of uv, pipx or pip installed Flexi is unknowable."""


def get_pypi_version() -> str | None:
    """The latest published version, or None if PyPI could not be read."""
    # Through a `Client`, like the bank-holiday fetch: `httpx.get` builds a
    # client of its own and goes straight to `Client.request`, bypassing the
    # `Client.get` seam every other outbound call here shares.
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            response = client.get(PYPI_URL)
        response.raise_for_status()
        return str(response.json()["info"]["version"])
    # The environment decides what `httpx.Client` raises before a request is
    # made: `ALL_PROXY=socks5://...` without the socks extra is an
    # `ImportError`, a proxy URL with a bad port an `httpx.InvalidURL`, an
    # `SSL_CERT_FILE` pointing at a removed bundle an `OSError`. An optional
    # update check owes the caller `None`, whatever the shell exports.
    except Exception:  # noqa: BLE001 - documented to return None for any failure
        return None


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
