"""Manage your working hours, flexibly."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

__all__ = ("__version__", "version")

if TYPE_CHECKING:
    # Declared for type checkers only; ``__getattr__`` below resolves and
    # caches it on first access.
    __version__: str


@cache
def version() -> str:
    """Return the installed Flexi version, resolving package metadata once.

    The metadata import stays inside the function: importing :mod:`flexi` is on
    every command's startup path, and most commands never ask for the version.
    """
    import importlib.metadata

    try:
        return importlib.metadata.version("flexi")
    except importlib.metadata.PackageNotFoundError:
        # A repackaged distribution can retain the flexi import package. Avoid
        # scanning installed distributions on the ordinary startup path.
        providers = importlib.metadata.packages_distributions().get("flexi", [])
        if len(providers) != 1:
            return "unknown"
        try:
            return importlib.metadata.version(providers[0])
        except importlib.metadata.PackageNotFoundError:
            return "unknown"


def __getattr__(name: str) -> str:
    """Resolve the compatibility ``__version__`` attribute lazily."""
    if name == "__version__":
        resolved = version()
        globals()[name] = resolved
        return resolved
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """Include lazy public attributes in interactive discovery."""
    return sorted(set(globals()) | set(__all__))
