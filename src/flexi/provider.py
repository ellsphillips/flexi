"""The command palette: every action, including the ones with no key.

The key strip shows a handful of bindings and the help screen shows all of
them. This lists actions, bound or not, down to booking one absence type on
one day.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from textual.command import DiscoveryHit, Hit, Hits, Provider

from flexi.components.chrome import NAV_ITEMS
from flexi.constants import AbsenceType, Granularity
from flexi.context import CommandApplication, command_app

__all__ = ("Command", "CommandCallback", "FlexiCommands", "commands")


type CommandCallback = Callable[[], object]
"""An action whose result the command palette ignores."""


@dataclass(frozen=True, slots=True)
class Command:
    """One palette entry."""

    title: str
    help: str
    run: CommandCallback


def commands(app: CommandApplication) -> tuple[Command, ...]:
    """Build the complete command catalogue for the application's current state.

    Outside :class:`FlexiCommands` so the catalogue can be inspected or adapted
    without constructing a Textual provider. The tuple is a snapshot: commands
    that depend on a dashboard appear only while one exists and is the
    destination in front of the user.
    """
    screen = app.dashboard()
    catalogue = [
        Command(
            "Clock in or out",
            "Toggle the clock. Bound to /",
            app.action_clock_toggle,
        ),
        Command("Help", "Every binding on this screen", app.action_help),
    ]

    if screen is None:
        # Setup has no dashboard, and every destination is drawn from the
        # period the dashboard holds.
        return tuple(catalogue)

    catalogue.extend(
        Command(
            f"Go to {item.label}",
            item.description,
            partial(app.action_go_to, item.screen),
        )
        for item in NAV_ITEMS
    )
    catalogue.append(
        Command(
            "Refresh bank holidays",
            "Re-fetch the GOV.UK calendar for the configured division",
            partial(app.refresh_holidays, force=True),
        )
    )

    if not app.showing_dashboard():
        # The period, the date under the cursor and the day a booking lands on
        # all belong to the dashboard, which Insights and Leave sit on top of
        # with spans of their own.
        return tuple(catalogue)

    catalogue.extend(
        Command(
            f"Period: {granularity.label.lower()}",
            f"Show one {granularity.value} at a time",
            partial(screen.action_zoom, granularity.value),
        )
        for granularity in Granularity
    )
    catalogue.extend(
        (
            Command("Go to today", "Return to the current period", screen.action_today),
            Command("Go to date…", "Jump the view to a date", screen.action_go_to_date),
            Command(
                "Book leave…",
                "Open the leave year and book on it directly",
                partial(app.action_go_to, "leave"),
            ),
        )
    )
    catalogue.extend(
        Command(
            f"Book {kind.phrase}…",
            f"Record {kind.phrase} on the selected day",
            partial(screen.open_absence_modal, screen.period.anchor, kind),
        )
        for kind in AbsenceType
    )
    return tuple(catalogue)


class FlexiCommands(Provider):
    """Textual's adapter over Flexi's public command catalogue."""

    async def discover(self) -> Hits:
        """Offer every command, before anything has been typed."""
        for command in commands(command_app(self.app)):
            yield DiscoveryHit(command.title, command.run, help=command.help)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for command in commands(command_app(self.app)):
            score = matcher.match(command.title)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(command.title),
                    command.run,
                    help=command.help,
                )
