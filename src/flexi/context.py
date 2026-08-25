"""Reaching the application from something mounted on it, with a type.

``docs/ARCHITECTURE.md`` asked for this and it was never written, so the widgets
and the command palette reached upwards with ``getattr(app, "action_go_to",
_noop)`` instead -- eighteen string lookups and a ``# type: ignore`` doing the
work of one cast. A renamed action stayed green under ``mypy --strict`` and
turned into a palette entry that silently did nothing, which is why
``tests/tui/test_provider.py`` opens by saying that nothing type-checks a
palette entry.

The import cycle those lookups were avoiding is real -- ``flexi.app`` imports
the screens, which import the components -- and ``TYPE_CHECKING`` is the answer
to it. The name is only ever needed by the type checker, because at runtime this
is a cast and a cast does nothing.

Not for a widget that is meant to work without Flexi behind it.
:class:`~flexi.components.chrome.AppHeader` is mounted bare in its own tests and
falls back rather than reaching; that is a widget being independent, not a
lookup avoiding a type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from textual.app import App as TextualApp

    from flexi.app import FlexiApp


def flexi_app(app: TextualApp[Any]) -> FlexiApp:
    """The running :class:`~flexi.app.FlexiApp`, typed.

    One cast, in one place, for everything that is only ever mounted inside
    Flexi. Anything reached through it -- ``services``, ``nav``, the actions --
    is checked from here on.
    """
    return cast("FlexiApp", app)
