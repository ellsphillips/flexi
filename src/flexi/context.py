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

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from textual.app import App as TextualApp
from textual.screen import Screen

from flexi.domain.period import Period

if TYPE_CHECKING:
    from flexi.app import FlexiApp as FlexiApplication
else:

    @runtime_checkable
    class FlexiApplication(Protocol):
        """Runtime-resolvable view of the application returned by :func:`flexi_app`.

        Static analysis sees the concrete :class:`flexi.app.FlexiApp`. Keeping
        the runtime side structural avoids importing the application back into
        the components that use this module solely to break that cycle.
        """

        services: object


__all__ = ("FlexiApplication", "ModuleHost", "flexi_app", "module_host")


@runtime_checkable
class ModuleHost(Protocol):
    """What a screen has to own before it can mount a module.

    Both of these were reached with ``getattr(self.screen, name, fallback)`` and
    a cast. Neither fallback was taken once in fifteen hundred tests and neither
    can be -- every screen that mounts a module sets both in ``__init__`` -- but
    the period's fallback still read the clock and built a week around it on
    every access, to compute a default that was then thrown away.

    Written as a protocol rather than a base class because the three screens
    already share no ancestor, and what a module needs of its host is these two
    attributes rather than an inheritance.
    """

    period: Period
    now: datetime


def module_host(screen: Screen[Any]) -> ModuleHost:
    """The screen a module is mounted on, typed as one that can host it."""
    if not isinstance(screen, ModuleHost):
        message = f"{screen!r} does not provide module period and time context"
        raise TypeError(message)
    return screen


def flexi_app(app: TextualApp[Any]) -> FlexiApplication:
    """The running :class:`~flexi.app.FlexiApp`, typed.

    One structural check, in one place, for everything that is only ever
    mounted inside Flexi. Anything reached through it -- ``services``, ``nav``,
    the actions -- is statically checked from here on, while a widget mounted
    on the wrong application fails at this boundary with a useful message.
    """
    if not isinstance(app, FlexiApplication):
        message = f"{app!r} does not provide the Flexi application context"
        raise TypeError(message)
    return app
