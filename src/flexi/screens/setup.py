from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Select, Static

from flexi.services.registry import Services

DIVISIONS = [
    ("England & Wales", "england-and-wales"),
    ("Scotland", "scotland"),
    ("Northern Ireland", "northern-ireland"),
]


class SetupScreen(Screen[bool]):
    """First-launch setup screen. Returns True when setup is saved."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    SetupScreen {
        align: center middle;
    }

    #setup-dialog {
        width: 64;
        height: auto;
        max-height: 32;
        border: thick $primary;
        padding: 1 2;
        background: $surface;
    }

    .setup-row {
        height: 3;
        layout: horizontal;
        margin-bottom: 1;

        Label {
            width: 26;
            padding-top: 1;
        }

        Input, Select {
            width: 1fr;
        }
    }

    .setup-buttons {
        height: 3;
        layout: horizontal;
        align: right middle;

        Button {
            margin-left: 1;
        }
    }
    """

    def __init__(self, services: Services, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._services = services
        self._settings_svc = services.settings

    def compose(self) -> ComposeResult:
        year = self._settings_svc.active_leave_year()
        with Container(id="setup-dialog"):
            yield Static("Welcome to Flexi! Complete setup to continue.\n")

            with Horizontal(classes="setup-row"):
                yield Label("Leave year start (MM-DD)")
                yield Input("01-01", id="input-leave-start", placeholder="MM-DD")

            with Horizontal(classes="setup-row"):
                yield Label(f"Entitlement {year} (days)")
                yield Input("25.0", id="input-entitlement", placeholder="25.0")

            with Horizontal(classes="setup-row"):
                yield Label("Working days")
                yield Input("Mon-Fri", id="input-working-days", placeholder="Mon-Fri")

            with Horizontal(classes="setup-row"):
                yield Label("Bank holiday region")
                yield Select(
                    DIVISIONS,
                    value="england-and-wales",
                    id="select-division",
                )

            with Horizontal(classes="setup-row"):
                yield Label("Auto-close time (HH:MM)")
                yield Input("18:00", id="input-auto-close", placeholder="HH:MM")

            with Horizontal(classes="setup-buttons"):
                yield Button("Save", id="btn-save", variant="primary")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self._save()

    def _save(self) -> None:
        leave_start = self.query_one("#input-leave-start", Input).value.strip()
        entitlement_str = self.query_one("#input-entitlement", Input).value.strip()
        working_days = self.query_one("#input-working-days", Input).value.strip()
        division = self.query_one("#select-division", Select).value
        auto_close = self.query_one("#input-auto-close", Input).value.strip()

        # Validate all required
        if not all([leave_start, entitlement_str, working_days, auto_close]):
            self.notify("All fields are required", severity="error")
            return

        try:
            entitlement = float(entitlement_str)
        except ValueError:
            self.notify("Invalid entitlement value", severity="error")
            return

        if not isinstance(division, str):
            self.notify("Please select a bank holiday region", severity="error")
            return

        try:
            self._settings_svc.save_settings(
                leave_year_start=leave_start,
                working_days=working_days,
                bank_holiday_division=division,
                auto_close_time=auto_close,
            )
        except ValueError as error:
            self.notify(str(error), severity="error")
            return

        # The leave year, not the calendar year. Setting Flexi up in February
        # against an April leave year files the allowance under the year that
        # has not started, and get_active_entitlement_days then finds nothing.
        year = self._settings_svc.active_leave_year()
        self._settings_svc.save_entitlement(year, entitlement)

        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
