"""Manage your working hours, flexibly."""

from typing import Any

__all__ = ["__version__"]


def __getattr__(name: str) -> Any:
    """Resolve ``__version__`` when something actually asks for it.

    ``importlib.metadata.version`` reads the installed distribution's metadata
    off disk and costs forty milliseconds. It ran at import of the package root
    -- which is to say on every command, including ``flexi clock in`` -- to
    answer a question only ``--version`` and the update check ever put.
    """
    if name == "__version__":
        import importlib.metadata

        try:
            return importlib.metadata.version("flexi")
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover
            return "unknown"
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
