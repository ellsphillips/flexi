"""What every dashboard module has in common.

A module is a titled, focusable panel that knows how to redraw itself and which
kinds of change are worth redrawing for. It never calls another module's
``rebuild()``; the screen does that, driven by :class:`~flexi.messages.Scope`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, TYPE_CHECKING, ClassVar, cast

from textual.widgets import Static

from flexi.domain.period import Granularity, Period
from flexi.messages import DataChanged, Scope

if TYPE_CHECKING:
    from flexi.services.registry import Services


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
        **kwargs: Any,
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
        return cast("Services", self.app.services)  # type: ignore[attr-defined]

    @property
    def period(self) -> Period:
        """The span the dashboard is currently showing."""
        fallback = Period.containing(date.today(), Granularity.WEEK)
        return cast(Period, getattr(self.screen, "period", fallback))

    @property
    def selected(self) -> date:
        """The date the dashboard is anchored on."""
        return self.period.anchor

    @property
    def now(self) -> datetime:
        """The moment this redraw is drawing, in one place so tests can fix it."""
        return cast(datetime, getattr(self.screen, "now", datetime.now()))

    # -- redrawing ---------------------------------------------------------

    def rebuild(self) -> None:
        """Redraw from the current data. Overridden by every module."""

    def rebuild_if(self, scope: Scope) -> None:
        """Redraw only when the change was one this module cares about."""
        if scope & self.WATCHES:
            self.rebuild()

    def announce(self, scope: Scope) -> None:
        """Tell the screen that something was written."""
        self.post_message(DataChanged(scope))

    def set_subtitle(self, text: str) -> None:
        """Write into the border subtitle — the module's live data slot."""
        self.border_subtitle = text
