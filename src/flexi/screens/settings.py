"""Changing the four answers given at setup, and the leave for each year.

The four shared questions are asked here and again on the first-run form, of the
same four widget ids -- so parsing them lives in :func:`parse_answers` rather
than in each screen, which is where the two wordings of the same refusal came
from.
"""

from __future__ import annotations

from typing import ClassVar, Unpack

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label, Select, Static

from flexi.components.options import ScreenOptions
from flexi.constants import Division
from flexi.domain.format import days as fmt_days
from flexi.domain.format import plural
from flexi.services.registry import Services
from flexi.services.settings import (
    DEFAULT_ENTITLEMENT_DAYS,
    SettingsUpdate,
    parse_settings,
)

__all__ = (
    "ALL_REQUIRED",
    "NO_DIVISION",
    "SettingsScreen",
    "parse_answers",
)

ALL_REQUIRED = "All fields are required"
NO_DIVISION = "Select a bank holiday region"


def parse_answers(node: Widget) -> SettingsUpdate:
    """Parse the four answers shared by setup and settings forms.

    No persistence happens here. Both forms can therefore validate all of
    their other fields before opening one settings transaction.

    A ``Select`` with nothing chosen answers its ``NULL`` sentinel rather than
    a string, which is why the division is checked separately from the text
    fields.
    """
    leave_start = node.query_one("#input-leave-start", Input).value.strip()
    working_days = node.query_one("#input-working-days", Input).value.strip()
    division = node.query_one("#select-division", Select).value
    auto_close = node.query_one("#input-auto-close", Input).value.strip()

    if not all([leave_start, working_days, auto_close]):
        raise ValueError(ALL_REQUIRED)
    if not isinstance(division, str):
        raise ValueError(NO_DIVISION)  # noqa: TRY004 - invalid user selection
    return parse_settings(
        leave_year_start=leave_start,
        working_days=working_days,
        bank_holiday_division=division,
        auto_close_time=auto_close,
    )


class SettingsScreen(Screen[bool]):
    """Settings edit screen. Returns True when saved."""

    HELP_LABEL = "Settings"

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "back", "Back")]

    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-dialog {
        width: 66;
        height: auto;
        max-height: 36;
        border: thick $primary;
        padding: 1 2;
        background: $surface;
    }
    .settings-row {
        height: 3;
        layout: horizontal;
        margin-bottom: 1;
        Label { width: 26; padding-top: 1; }
        Input, Select { width: 1fr; }
    }
    .entitlement-row {
        height: 3;
        layout: horizontal;
        margin-bottom: 1;
        Label { width: 12; padding-top: 1; }
        Input { width: 1fr; }
    }
    .settings-buttons {
        height: 3;
        layout: horizontal;
        align: right middle;
        Button { margin-left: 1; }
    }
    """

    def __init__(self, services: Services, **kwargs: Unpack[ScreenOptions]) -> None:
        super().__init__(**kwargs)
        self._svc = services.settings

    def compose(self) -> ComposeResult:
        # Every field through the service's own accessor, which is where each
        # one's fallback is written down. Read from the row instead, this screen
        # carried a second copy of all four -- and one of them, the region, was
        # a slug compared against a member that never matched.
        month, day = self._svc.get_leave_year_start()
        leave_start = f"{month:02d}-{day:02d}"
        working = ",".join(str(index) for index in self._svc.get_working_day_indices())
        division = self._svc.get_division().value
        auto_close = f"{self._svc.get_auto_close_time():%H:%M}"

        with Container(id="settings-dialog"):
            yield Static("Settings\n")

            with Horizontal(classes="settings-row"):
                yield Label("Leave year start")
                yield Input(leave_start, id="input-leave-start")

            with Horizontal(classes="settings-row"):
                yield Label("Working days")
                yield Input(working, id="input-working-days")

            with Horizontal(classes="settings-row"):
                yield Label("Bank holiday region")
                yield Select(Division.choices(), value=division, id="select-division")

            with Horizontal(classes="settings-row"):
                yield Label("Auto-close time")
                yield Input(auto_close, id="input-auto-close")

            yield Static("\nEntitlements by year:")
            with Vertical(id="entitlements-list"):
                for ent in self._svc.all_entitlements():
                    with Horizontal(classes="entitlement-row"):
                        yield Label(str(ent.year))
                        yield Input(
                            str(ent.days),
                            id=f"ent-{ent.year}",
                        )

            with Horizontal(classes="settings-buttons"):
                yield Button("Add Next Year", id="btn-add-year")
                yield Button("Back", id="btn-back", variant="default")
                yield Button("Save", id="btn-save", variant="primary")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.dismiss(False)
        elif event.button.id == "btn-save":
            self._save()
        elif event.button.id == "btn-add-year":
            self._add_next_year()

    def _add_next_year(self) -> None:
        """Add a year to the list, and stay on the screen.

        It used to dismiss, which looked like a refresh and was an exit: every
        field typed into the form above went with it, unsaved and unmentioned,
        and dismissing with ``True`` told the application settings had been
        changed. Mounting the row is what "refresh screen" meant.
        """
        years = self._svc.all_entitlements()
        if years:
            next_year = years[-1].year + 1
            default_days = years[-1].days
        else:
            next_year = self._svc.active_leave_year()
            default_days = DEFAULT_ENTITLEMENT_DAYS

        self._svc.save_entitlement(next_year, default_days)
        self.query_one("#entitlements-list", Vertical).mount(
            Horizontal(
                Label(str(next_year)),
                Input(str(default_days), id=f"ent-{next_year}"),
                classes="entitlement-row",
            )
        )
        self.notify(
            f"Added {next_year} with {fmt_days(default_days)}"
            f" {plural(default_days, 'day')}"
        )

    def _save(self) -> None:
        """Write every field, or none of them.

        The entitlements used to be parsed after `save_settings` had already
        committed, so a year somebody could not type left the working pattern
        and the region written to the database, the screen open, and the ledger
        cache holding figures built against the settings that had just been
        replaced. Nothing invalidates it on this path: the application hangs
        that off `dismiss(True)`, and a rejection does not dismiss.
        """
        allowances: dict[int, float] = {}
        rejected: list[str] = []
        for entitlement in self._svc.all_entitlements():
            field = self.query_one(f"#ent-{entitlement.year}", Input)
            try:
                allowances[entitlement.year] = float(field.value)
            except ValueError:
                rejected.append(str(entitlement.year))

        if rejected:
            self.notify(
                f"Leave for {', '.join(rejected)} must be a number of days",
                severity="error",
            )
            return

        try:
            update = parse_answers(self)
        except ValueError as error:
            self.notify(str(error), severity="error")
            return

        self._svc.save_settings_and_entitlements(update, allowances)

        self.dismiss(True)

    def action_back(self) -> None:
        self.dismiss(False)
