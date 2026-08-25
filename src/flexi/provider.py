"""The command palette: everything, including what has no key.

The key strip shows six or seven bindings, the help screen shows every binding,
and this shows every *action* — including the ones that never earned a key, like
booking a specific absence type on a specific day. It is the long tail, and it is
why the keymap can stay small.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from textual.command import DiscoveryHit, Hit, Hits, Provider

from flexi.components.chrome import NAV_ITEMS
from flexi.constants import AbsenceType
from flexi.context import flexi_app
from flexi.domain.period import Granularity

if TYPE_CHECKING:
    from flexi.app import FlexiApp


class FlexiCommands(Provider):
    """Flexi's own palette entries, alongside Textual's built-in ones."""

    async def discover(self) -> Hits:
        """What the palette offers before anything has been typed."""
        for command in self._commands():
            yield DiscoveryHit(command.title, command.run, help=command.help)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for command in self._commands():
            score = matcher.match(command.title)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(command.title),
                    command.run,
                    help=command.help,
                )

    # -- the catalogue -----------------------------------------------------

    def _commands(self) -> Iterable[_Command]:
        app = flexi_app(self.app)
        screen = app.dashboard()

        yield _Command(
            "Clock in or out",
            "Toggle the clock. Bound to /",
            app.action_clock_toggle,
        )
        yield _Command("Help", "Every binding on this screen", app.action_help)

        for item in NAV_ITEMS:
            yield _Command(
                f"Go to {item.label}",
                item.description,
                partial(app.action_go_to, item.screen),
            )

        if screen is None:
            return

        for granularity in Granularity:
            yield _Command(
                f"Period: {granularity.label.lower()}",
                f"Show one {granularity.value} at a time",
                partial(screen.action_zoom, granularity.value),
            )
        yield _Command(
            "Go to today", "Return to the current period", screen.action_today
        )
        yield _Command(
            "Go to date…", "Jump the view to a date", screen.action_go_to_date
        )

        yield _Command(
            "Book leave…",
            "Open the leave year and book on it directly",
            partial(app.action_go_to, "leave"),
        )

        for kind in AbsenceType:
            yield _Command(
                f"Book {kind.phrase}…",
                f"Record {kind.phrase} on the selected day",
                partial(screen.open_absence_modal, screen.period.anchor, kind),
            )

        yield _Command(
            "Refresh bank holidays",
            "Re-fetch the GOV.UK calendar for the configured division",
            partial(_refresh_holidays, app),
        )


@dataclass(frozen=True, slots=True)
class _Command:
    """One palette entry."""

    title: str
    help: str
    run: Callable[[], Any]


def _refresh_holidays(app: FlexiApp) -> None:
    ok = app.services.bank_holidays.fetch_and_cache()
    app.services.invalidate()
    app.notify(
        "Bank holidays refreshed" if ok else "Could not reach gov.uk",
        severity="information" if ok else "warning",
        timeout=4,
    )
