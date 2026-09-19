"""Shared behaviour for every dashboard module.

A module is a titled, focusable panel that knows how to redraw itself and which
kinds of change are worth redrawing for. It never calls another module's
``rebuild()``; the screen does that, driven by :class:`~flexi.messages.Scope`.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Unpack

from textual.widget import Widget
from textual.widgets import Static

from flexi.components.options import ModuleOptions
from flexi.context import ServiceRegistry, module_host, service_app
from flexi.domain.period import Period
from flexi.messages import Scope

__all__ = ("Module",)


class Module(Static):
    """A titled panel on the dashboard.

    Subclasses set :attr:`WATCHES` to the scopes they care about and implement
    :meth:`rebuild`. The border title, the focus behaviour and the route to the
    services are here.
    """

    WATCHES: ClassVar[Scope] = Scope.ALL

    BENTO: ClassVar[str] = ""
    """Extra classes saying how much of a grid this island needs.

    Declared by the module, not by the screen laying it out: how wide it has to
    be to read is a fact about the module.
    """

    can_focus = True

    def __init__(
        self,
        *,
        id: str,  # noqa: A002 - Textual's own parameter name
        title: str,
        subtitle: str = "",
        **kwargs: Unpack[ModuleOptions],
    ) -> None:
        super().__init__(id=id, classes=f"module {self.BENTO}".strip(), **kwargs)
        # Plain assignment routes through Static's reactive machinery before the
        # widget is mounted, and the title is silently lost.
        super().__setattr__("border_title", title)
        super().__setattr__("border_subtitle", subtitle)

    # context ---------------------------------------------------------------

    @property
    def services(self) -> ServiceRegistry:
        """The application's service registry."""
        return service_app(self.app).services

    @property
    def period(self) -> Period:
        """The span the screen below is currently showing."""
        return module_host(self.screen).period

    @property
    def now(self) -> datetime:
        """The moment this redraw is drawing."""
        return module_host(self.screen).now

    # redrawing -------------------------------------------------------------

    def rebuild(self) -> None:
        """Redraw from the current data. Overridden by every module."""
        raise NotImplementedError

    def rebuild_if(self, scope: Scope) -> None:
        """Redraw only when the change was one this module cares about."""
        if scope & self.WATCHES:
            self.rebuild()

    def focus_target(self) -> Widget:
        """The widget a jump to this module should focus.

        Usually the module itself; a module whose content is a table overrides
        this to return the table, so a jump lands in the rows.
        """
        return self

    def set_subtitle(self, text: str) -> None:
        """Write into the border subtitle, the module's live data slot."""
        self.border_subtitle = text
