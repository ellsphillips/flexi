"""The lightweight, typed API exposed directly by :mod:`flexi`."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from collections.abc import Iterator
from typing import assert_type, get_type_hints
from unittest.mock import patch

import pytest

import flexi


@pytest.fixture
def unresolved_version(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Return the package metadata API to its just-imported state."""
    flexi.version.cache_clear()
    monkeypatch.delitem(vars(flexi), "__version__", raising=False)
    yield
    flexi.version.cache_clear()
    monkeypatch.delitem(vars(flexi), "__version__", raising=False)


def test_the_public_root_api_is_typed_and_discoverable(
    unresolved_version: None,
) -> None:
    with patch.object(importlib.metadata, "version", return_value="1.2.3"):
        assert flexi.__all__ == ("__version__", "version")
        assert {"__version__", "version"} <= set(dir(flexi))
        assert get_type_hints(flexi.version) == {"return": str}
        assert assert_type(flexi.version(), str) == "1.2.3"
        assert assert_type(flexi.__version__, str) == "1.2.3"


def test_version_metadata_is_resolved_once(unresolved_version: None) -> None:
    with patch.object(importlib.metadata, "version", return_value="4.5.6") as resolve:
        assert flexi.version() == "4.5.6"
        assert flexi.version() == "4.5.6"
        assert flexi.__version__ == "4.5.6"
        assert flexi.__version__ == "4.5.6"

    resolve.assert_called_once_with("flexi")
    assert vars(flexi)["__version__"] == "4.5.6"


def test_version_has_a_source_checkout_fallback(unresolved_version: None) -> None:
    missing = importlib.metadata.PackageNotFoundError("flexi")
    with patch.object(importlib.metadata, "version", side_effect=missing) as resolve:
        assert flexi.version() == "unknown"
        assert flexi.__version__ == "unknown"

    resolve.assert_called_once_with("flexi")


def test_an_unknown_root_attribute_still_raises(unresolved_version: None) -> None:
    name = "not_an_api"
    with pytest.raises(
        AttributeError,
        match=r"module 'flexi' has no attribute 'not_an_api'",
    ):
        getattr(flexi, name)


def test_importing_the_root_is_lazy_and_lightweight() -> None:
    """A fresh interpreter catches imports already loaded by this test worker."""
    script = """
import importlib.metadata
import sys
from unittest.mock import patch

with patch.object(
    importlib.metadata,
    "version",
    side_effect=AssertionError("package metadata was read during import"),
):
    import flexi

heavy = {"alembic", "httpx", "sqlalchemy", "textual"}
loaded = heavy.intersection(sys.modules)
if loaded:
    raise AssertionError(f"root import loaded heavy dependencies: {sorted(loaded)}")
"""
    subprocess.run(  # noqa: S603 - fixed interpreter and in-repository script
        [sys.executable, "-c", script], check=True
    )
