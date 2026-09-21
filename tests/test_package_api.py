"""The lightweight, typed API exposed directly by :mod:`flexi`."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import assert_type, get_type_hints
from unittest.mock import call, patch

import pytest

import flexi


@pytest.fixture
def unresolved_version() -> Iterator[None]:
    """Return the package metadata API to its just-imported state.

    Plainly, not through `monkeypatch`: `delitem` restores the value it removed
    at teardown, which here is the patched one the test installed.

    `flexi.__version__` is resolved once and cached in the module's globals, so
    a version invented for one test is read by every later test in the worker.
    """
    _forget()
    yield
    _forget()


def _forget() -> None:
    flexi.version.cache_clear()
    vars(flexi).pop("__version__", None)


def test_root_api_is_typed_and_discoverable(unresolved_version: None) -> None:
    with patch.object(importlib.metadata, "version", return_value="1.2.3"):
        assert flexi.__all__ == ("__version__", "version")
        assert {"__version__", "version"} <= set(dir(flexi))
        assert get_type_hints(flexi.version) == {"return": str}
        assert assert_type(flexi.version(), str) == "1.2.3"
        assert assert_type(flexi.__version__, str) == "1.2.3"


def test_version_metadata_is_resolved_once(unresolved_version: None) -> None:
    with (
        patch.object(importlib.metadata, "version", return_value="4.5.6") as resolve,
        patch.object(importlib.metadata, "packages_distributions") as scan,
    ):
        assert flexi.version() == "4.5.6"
        assert flexi.version() == "4.5.6"
        assert flexi.__version__ == "4.5.6"
        assert flexi.__version__ == "4.5.6"

    resolve.assert_called_once_with("flexi")
    scan.assert_not_called()
    assert vars(flexi)["__version__"] == "4.5.6"


@pytest.mark.parametrize(
    "providers", [{}, {"flexi": []}, {"flexi": ["first", "second"]}]
)
def test_version_has_a_source_checkout_fallback(
    unresolved_version: None, providers: dict[str, list[str]]
) -> None:
    missing = importlib.metadata.PackageNotFoundError("flexi")
    with (
        patch.object(importlib.metadata, "version", side_effect=missing) as resolve,
        patch.object(
            importlib.metadata, "packages_distributions", return_value=providers
        ) as scan,
    ):
        assert flexi.version() == "unknown"
        assert flexi.__version__ == "unknown"

    resolve.assert_called_once_with("flexi")
    scan.assert_called_once_with()


@pytest.mark.parametrize("provider_version", ["7.8.9", None])
def test_version_resolves_only_one_renamed_provider(
    unresolved_version: None, provider_version: str | None
) -> None:
    missing = importlib.metadata.PackageNotFoundError("flexi")
    with (
        patch.object(
            importlib.metadata,
            "version",
            side_effect=[missing, provider_version or missing],
        ) as resolve,
        patch.object(
            importlib.metadata,
            "packages_distributions",
            return_value={"flexi": ["repacked-flexi"]},
        ) as scan,
    ):
        assert flexi.version() == (provider_version or "unknown")
        assert flexi.__version__ == (provider_version or "unknown")

    assert resolve.call_args_list == [call("flexi"), call("repacked-flexi")]
    scan.assert_called_once_with()


def test_version_reads_installed_renamed_distribution(tmp_path: Path) -> None:
    installed = tmp_path / "repacked_flexi-7.8.9.dist-info"
    installed.mkdir()
    (installed / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: repacked-flexi\nVersion: 7.8.9\n",
        encoding="utf-8",
    )
    (installed / "top_level.txt").write_text("flexi\n", encoding="utf-8")
    script = """
import sys
sys.path[:0] = sys.argv[1:]
import flexi
assert "importlib.metadata" not in sys.modules, "root import read metadata eagerly"
assert flexi.version() == "7.8.9"
assert flexi.__version__ == "7.8.9"
"""
    subprocess.run(  # noqa: S603 - isolated interpreter and controlled metadata
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            script,
            str(Path(flexi.__file__).parent.parent),
            str(tmp_path),
        ],
        check=True,
    )


def test_unknown_root_attribute_raises(unresolved_version: None) -> None:
    name = "not_an_api"
    with pytest.raises(
        AttributeError,
        match=r"module 'flexi' has no attribute 'not_an_api'",
    ):
        getattr(flexi, name)


def test_importing_the_root_is_lazy_and_lightweight() -> None:
    """A fresh interpreter, so imports already loaded by this worker do not hide."""
    script = """
import importlib.metadata
import sys
from unittest.mock import patch

with (
    patch.object(
        importlib.metadata,
        "version",
        side_effect=AssertionError("package metadata was read during import"),
    ),
    patch.object(
        importlib.metadata,
        "packages_distributions",
        side_effect=AssertionError("package providers were scanned during import"),
    ),
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
