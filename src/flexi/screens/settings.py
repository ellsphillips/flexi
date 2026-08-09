from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Select, Static

from flexi import wallclock
from flexi.services.registry import Services

DIVISIONS = [
    ("England & Wales", "england-and-wales"),
    ("Scotland", "scotland"),
    ("Northern Ireland", "northern-ireland"),
]


class SettingsScreen(Screen[bool]):
    """Settings edit screen. Returns True when saved."""

    BINDINGS = [("escape", "back", "Back")]

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

    def __init__(self, services: Services, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._services = services
        self._svc = services.settings

    def compose(self) -> ComposeResult:
        settings = self._svc.get_settings()
        leave_start = settings.leave_year_start if settings else "01-01"
        working = settings.working_days if settings else "0,1,2,3,4"
        division = settings.bank_holiday_division if settings else "england-and-wales"
        auto_close = settings.auto_close_time if settings else "18:00"

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
                yield Select(DIVISIONS, value=division, id="select-division")

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
        ents = self._svc.all_entitlements()
        if ents:
            last = ents[-1]
            next_year = last.year + 1
            default_days = last.days
        else:
            next_year = wallclock.today().year
            default_days = 25.0

        self._svc.save_entitlement(next_year, default_days)
        self.notify(f"Added {next_year} with {default_days} days")
        # Refresh screen
        self.dismiss(True)

    def _save(self) -> None:
        leave_start = self.query_one("#input-leave-start", Input).value.strip()
        working_days = self.query_one("#input-working-days", Input).value.strip()
        division = self.query_one("#select-division", Select).value
        auto_close = self.query_one("#input-auto-close", Input).value.strip()

        if not all([leave_start, working_days, auto_close]):
            self.notify("All fields are required", severity="error")
            return
        if not isinstance(division, str):
            self.notify("Select a bank holiday region", severity="error")
            return

        from flexi.services.settings import parse_month_day

        try:
            parse_month_day(leave_start)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return

        self._svc.save_settings(
            leave_year_start=leave_start,
            working_days=working_days,
            bank_holiday_division=division,
            auto_close_time=auto_close,
        )

        # Save entitlement updates
        for ent in self._svc.all_entitlements():
            try:
                inp = self.query_one(f"#ent-{ent.year}", Input)
                self._svc.save_entitlement(ent.year, float(inp.value))
            except Exception:  # noqa: BLE001
                pass

        self.dismiss(True)

    def action_back(self) -> None:
        self.dismiss(False)
