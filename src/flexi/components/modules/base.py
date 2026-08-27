"""What every dashboard module has in common.

A module is a titled, focusable panel that knows how to redraw itself and which
kinds of change are worth redrawing for. It never calls another module's
``rebuild()``; the screen does that, driven by :class:`~flexi.messages.Scope`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Unpack

from textual.widget import Widget
from textual.widgets import Static

from flexi.components.options import ModuleOptions
from flexi.context import module_host, service_app
from flexi.domain.period import Period
from flexi.messages import Scope

if TYPE_CHECKING:
    from flexi.services.registry import Services

__all__ = ("Module",)


class Module(Static):
    """A titled panel on the dashboard.

    Subclasses set :attr:`WATCHES` to the scopes they care about and implement
    :meth:`rebuild`. Everything else — the border title, the focus behaviour, the
    route to the services — is here so five modules do not each invent it.
    """

    WATCHES: ClassVar[Scope] = Scope.ALL

    can_focus = True

    def __init__(
        self,
        *,
        id: str,  # noqa: A002 - Textual's own parameter name
        title: str,
        subtitle: str = "",
        **kwargs: Unpack[ModuleOptions],
    ) -> None:
        super().__init__(id=id, classes="module", **kwargs)
        # Plain assignment routes through Static's reactive machinery before the
        # widget is mounted, and the title is silently lost.
        super().__setattr__("border_title", title)
        super().__setattr__("border_subtitle", subtitle)

    # -- context -----------------------------------------------------------

    @property
    def services(self) -> Services:
        """The application's service registry."""
        return service_app(self.app).services

    @property
    def period(self) -> Period:
        """The span the screen below is currently showing."""
        return module_host(self.screen).period

    @property
    def now(self) -> datetime:
        """The moment this redraw is drawing, in one place so tests can fix it."""
        return module_host(self.screen).now

    # -- redrawing ---------------------------------------------------------

    def rebuild(self) -> None:
        """Redraw from the current data. Overridden by every module."""

    def rebuild_if(self, scope: Scope) -> None:
        """Redraw only when the change was one this module cares about."""
        if scope & self.WATCHES:
            self.rebuild()

    def focus_target(self) -> Widget:
        """The widget a jump to this module should focus.

        Usually the module itself. A module whose content is a table wants the
        table — landing on the panel and then needing a second key to get into
        the rows is exactly the friction jump mode exists to remove.
        """
        return self

    def set_subtitle(self, text: str) -> None:
        """Write into the border subtitle — the module's live data slot."""
        self.border_subtitle = text
