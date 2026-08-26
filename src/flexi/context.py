"""Reaching the application from something mounted on it, with a type.

The palette and the modules reached upwards with ``getattr(app, "action_go_to",
noop)`` -- string lookups with a do-nothing fallback behind them. A renamed
action stayed green under ``mypy --strict`` and became a palette entry that
silently did nothing, which is the one failure a command palette cannot afford:
there is no error, no log line, and nothing on screen.

The import cycle those lookups were avoiding is real -- ``flexi.app`` imports
``flexi.provider`` -- and ``TYPE_CHECKING`` is the answer to it. The name is only
ever needed by the type checker, because at runtime this is a cast and a cast
does nothing.

Not for a widget meant to work without Flexi behind it. A component mounted bare
in its own tests should fall back rather than reach; that is independence, not a
lookup avoiding a type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from textual.app import App as TextualApp

    from flexi.app import FlexiApp


def flexi_app(app: TextualApp[Any]) -> FlexiApp:
    """The running :class:`~flexi.app.FlexiApp`, typed.

    One cast, in one place, for everything only ever mounted inside Flexi.
    Anything reached through it -- ``services``, the actions -- is checked from
    here on.
    """
    return cast("FlexiApp", app)
