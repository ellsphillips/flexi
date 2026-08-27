"""Manage your working hours, flexibly."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

__all__ = ("__version__", "version")

if TYPE_CHECKING:
    # Present to static consumers while remaining absent at runtime until first
    # access, when ``__getattr__`` resolves and installs it below.
    __version__: str


@cache
def version() -> str:
    """Return the installed Flexi version, resolving package metadata once.

    Metadata is deliberately imported and read here rather than at module scope.
    Most commands never ask for the version, and importing :mod:`flexi` is on
    every command's startup path.
    """
    import importlib.metadata

    try:
        return importlib.metadata.version("flexi")
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
