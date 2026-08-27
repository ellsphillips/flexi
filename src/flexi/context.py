"""Typed structural boundaries for objects owned by the Textual runtime.

Widgets and command providers receive Textual's broad ``App`` and ``Screen``
types even though they rely on much smaller Flexi-specific capabilities. The
protocols in this module name those capabilities without importing
``flexi.app`` back through the presentation graph, and the adapter functions
validate them once at the edge.

The service and command contracts are deliberately separate. A dashboard
module needs the service registry but none of the application's navigation
actions; the command palette needs those actions but never reaches into the
registry. Keeping those interfaces narrow lets either concern be hosted and
tested independently.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from textual.app import App as TextualApp
from textual.screen import Screen

from flexi.constants import AbsenceType
from flexi.domain.period import Period

if TYPE_CHECKING:
    from flexi.services.registry import Services

__all__ = (
    "CommandApplication",
    "CommandDashboard",
    "FlexiApplication",
    "ModuleHost",
    "ServiceApplication",
    "command_app",
    "flexi_app",
    "module_host",
    "service_app",
)


@runtime_checkable
class CommandDashboard(Protocol):
    """The dashboard operations exposed through the command palette."""

    period: Period

    def action_zoom(self, granularity: str) -> None:
        """Show the requested period granularity."""
        ...

    def action_today(self) -> None:
        """Move the current period to today."""
        ...

    def action_go_to_date(self) -> None:
        """Open the date navigation prompt."""
        ...

    def open_absence_modal(self, when: date, kind: AbsenceType) -> None:
        """Open a booking prompt for ``kind`` on ``when``."""
        ...


@runtime_checkable
class CommandApplication(Protocol):
    """The application operations exposed through the command palette."""

    def dashboard(self) -> CommandDashboard | None:
        """Return the mounted dashboard, if it is available."""
        ...

    def action_clock_toggle(self) -> None:
        """Toggle the current clock state."""
        ...

    def action_help(self) -> None:
        """Open the binding reference."""
        ...

    def action_go_to(self, name: str) -> None:
        """Navigate to a named destination."""
        ...

    def refresh_holidays(self, *, force: bool = False) -> None:
        """Refresh the cached bank-holiday calendar."""
        ...


if TYPE_CHECKING:

    @runtime_checkable
    class ServiceApplication(Protocol):
        """An application that owns Flexi's service registry."""

        services: Services

else:

    @runtime_checkable
    class ServiceApplication(Protocol):
        """An application that owns Flexi's service registry."""

        # The concrete registry is intentionally not imported at runtime:
        # doing so would load SQLAlchemy whenever a presentation component is
        # imported. Runtime structural checks need only prove ownership; the
        # TYPE_CHECKING definition above supplies the complete static type.
        services: object


@runtime_checkable
class FlexiApplication(ServiceApplication, CommandApplication, Protocol):
    """The complete application contract, composed from its narrow facets."""


@runtime_checkable
class ModuleHost(Protocol):
    """The temporal context a screen provides to a dashboard module."""

    period: Period
    now: datetime


def module_host(screen: Screen[Any]) -> ModuleHost:
    """Return ``screen`` as a module host, or fail at the context boundary."""
    if not isinstance(screen, ModuleHost):
        message = f"{screen!r} does not provide module period and time context"
        raise TypeError(message)
    return screen


def service_app(app: TextualApp[Any]) -> ServiceApplication:
    """Return an app that owns the service registry required by modules."""
    if not isinstance(app, ServiceApplication):
        message = f"{app!r} does not provide the Flexi service context"
        raise TypeError(message)
    return app


def command_app(app: TextualApp[Any]) -> CommandApplication:
    """Return an app that implements every command-palette operation."""
    if not isinstance(app, CommandApplication):
        message = f"{app!r} does not provide the Flexi command context"
        raise TypeError(message)
    return app


def flexi_app(app: TextualApp[Any]) -> FlexiApplication:
    """Return an app implementing the complete composed Flexi contract."""
    if not isinstance(app, FlexiApplication):
        message = f"{app!r} does not provide the complete Flexi application context"
        raise TypeError(message)
    return app
