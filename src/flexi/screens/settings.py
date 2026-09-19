"""Changing the four answers given at setup, and the leave for each year.

The first-run form asks the same four questions of the same four widget ids, so
parsing them lives in :func:`parse_answers` and both screens refuse in the same
words.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar, Unpack

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label, Select, Static

from flexi.components.options import ScreenOptions
from flexi.constants import Division
from flexi.domain.dates import DAY_NAMES
from flexi.services.registry import Services
from flexi.services.settings import (
    DEFAULT_ENTITLEMENT_DAYS,
    SettingsUpdate,
    parse_entitlement_days,
    parse_settings,
)

__all__ = (
    "ALL_REQUIRED",
    "NO_DIVISION",
    "SettingsScreen",
    "describe_working_days",
    "parse_answers",
)

ALL_REQUIRED = "All fields are required"
NO_DIVISION = "Select a bank holiday region"


def parse_answers(node: Widget) -> SettingsUpdate:
    """Parse the four answers shared by setup and settings forms.

    Nothing is persisted here, so both forms can validate all their other
    fields before opening one settings transaction.

    A ``Select`` with nothing chosen answers its ``NULL`` sentinel, not a
    string, which is why the division is checked separately from the text
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


_COLLAPSE_FROM = 3
"""Days from which a run reads better as `Mon-Fri` than as a list of names."""


def describe_working_days(days: Sequence[int]) -> str:
    """Weekday indices as the names the same field takes back.

    The form reads `Mon-Fri` and `0,1,2,3,4` alike. Names are shown because
    `1,2,3,4,5` reads as Monday to Friday and means Tuesday to Saturday.
    """
    names = [DAY_NAMES[day][:3].capitalize() for day in days]
    if len(names) >= _COLLAPSE_FROM and list(days) == list(
        range(days[0], days[-1] + 1)
    ):
        return f"{names[0]}-{names[-1]}"
    return ", ".join(names)


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
        max-height: 90%;
        border: thick $primary;
        padding: 1 2;
        background: $surface;
    }
    /* The questions scroll and the buttons do not: a clipped row still takes
       focus, so tab can reach a field that is off screen. */
    #settings-body {
        height: 1fr;
    }
    #entitlements-list { height: auto; }
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
        dock: bottom;
        height: 3;
        layout: horizontal;
        align: right middle;
        Button { margin-left: 1; }
    }
    """

    def __init__(self, services: Services, **kwargs: Unpack[ScreenOptions]) -> None:
        super().__init__(**kwargs)
        self._svc = services.settings
        self.entitlement_drafts = {
            entitlement.year: str(entitlement.days)
            for entitlement in self._svc.all_entitlements()
        }
        """Displayed entitlement years and their initial, uncommitted text."""

    def compose(self) -> ComposeResult:
        # Every field through the service's own accessor, which is where each
        # one's fallback is written down; the settings row alone does not carry
        # them.
        month, day = self._svc.get_leave_year_start()
        leave_start = f"{month:02d}-{day:02d}"
        working = describe_working_days(self._svc.get_working_day_indices())
        division = self._svc.get_division().value
        auto_close = f"{self._svc.get_auto_close_time():%H:%M}"

        with Container(id="settings-dialog"):
            with VerticalScroll(id="settings-body"):
                yield Static("Settings\n")

                with Horizontal(classes="settings-row"):
                    yield Label("Leave year start")
                    yield Input(leave_start, id="input-leave-start")

                with Horizontal(classes="settings-row"):
                    yield Label("Working days")
                    yield Input(working, id="input-working-days", placeholder="Mon-Fri")

                with Horizontal(classes="settings-row"):
                    yield Label("Bank holiday region")
                    yield Select(
                        Division.choices(), value=division, id="select-division"
                    )

                with Horizontal(classes="settings-row"):
                    yield Label("Auto-close time")
                    yield Input(auto_close, id="input-auto-close")

                yield Static("\nEntitlements by year:")
                with Vertical(id="entitlements-list"):
                    for year, days in self.entitlement_drafts.items():
                        with Horizontal(classes="entitlement-row"):
                            yield Label(str(year))
                            yield Input(days, id=f"ent-{year}")

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
        """Add an uncommitted year to the list, and stay on the screen.

        The row is a draft like every other field on the form, and Save is the
        only thing that writes.
        """
        if self.entitlement_drafts:
            latest = max(self.entitlement_drafts)
            next_year = latest + 1
            default_days = self.query_one(f"#ent-{latest}", Input).value
        else:
            next_year = self._svc.active_leave_year()
            default_days = str(DEFAULT_ENTITLEMENT_DAYS)

        self.entitlement_drafts[next_year] = default_days
        row = Horizontal(
            Label(str(next_year)),
            Input(default_days, id=f"ent-{next_year}"),
            classes="entitlement-row",
        )
        self.query_one("#entitlements-list", Vertical).mount(row)
        self.call_after_refresh(row.scroll_visible)
        self.notify(
            f"Added {next_year}; save to keep it",
        )

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        """Enter saves, as it does on the first-run form and in every modal."""
        self._save()

    def _save(self) -> None:
        """Write every field, or none of them.

        Every entitlement is parsed before anything is written, so a year that
        cannot be read leaves nothing written. Nothing invalidates the ledger
        cache on this path: the application hangs that off `dismiss(True)`, and
        a rejection does not dismiss.
        """
        allowances: dict[int, float] = {}
        rejected: list[str] = []
        for year in self.entitlement_drafts:
            field = self.query_one(f"#ent-{year}", Input)
            try:
                allowances[year] = parse_entitlement_days(field.value)
            except ValueError:
                rejected.append(str(year))

        if rejected:
            self.notify(
                f"Leave for {', '.join(rejected)} must be finite, "
                "non-negative numbers of days",
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
