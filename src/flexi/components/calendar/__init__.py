from datetime import datetime, timedelta
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Label, Static

from flexi.util.style import load_css


class CalendarDimensions:
    """Dimensions for the calendar component."""

    days: int = 7
    weeks: int = 6


def get_month_days(date: datetime) -> list[tuple[datetime, bool]]:
    """Returns list of (date, is_current_month) tuples for a calendar month and bleed."""
    first_day = date.replace(day=1)
    days_before = first_day.weekday() % CalendarDimensions.days
    start_date = first_day - timedelta(days=days_before)

    days: list[tuple[datetime, bool]] = []
    for i in range(42):
        curr_date = start_date + timedelta(days=i)
        is_current = curr_date.month == date.month
        days.append((curr_date, is_current))

    return days


class Calendar(Static):
    """The calendar island."""

    can_focus = True

    DEFAULT_CSS = load_css(__file__)

    def __init__(self, parent: Static, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs, classes="island")
        super().__setattr__("border_title", "Calendar")
        super().__setattr__("border_subtitle", "← . →")

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        today = datetime.now()

        calendar_days = get_month_days(today)

        calendar_rows = self.query(".calendar-row")

        for row_idx, row in enumerate(calendar_rows):
            row.remove_class("target_week")
            for col_idx, label in enumerate(row.query("Label")):
                day_idx = row_idx * CalendarDimensions.days + col_idx
                if day_idx >= len(calendar_days):
                    continue

                date, is_current = calendar_days[day_idx]

                label.update(str(date.day))  # pyright: ignore
                label.set_classes("")

                if not is_current:
                    label.add_class("not_current_month")

                if date.date() == today.date():
                    label.add_class("today")

                if date.date() == today.date():
                    label.add_class("target")

                days_to_first = today.weekday() % CalendarDimensions.days
                week_start = today - timedelta(days=days_to_first)
                week_end = week_start + timedelta(days=6)
                if week_start.date() <= date.date() <= week_end.date():
                    row.add_class("target_week")

    def compose(self) -> ComposeResult:
        with Container(id="calendar-container"):
            with Container(classes="month-selector"):
                yield Button("<", id="prev-month")
                yield Label("Current Month", classes="current-filter-label")
                yield Button(">", id="next-month")

            with Static(classes="calendar"):
                with Horizontal(classes="calendar-dotw-row"):
                    days = ["M", "T", "W", "T", "F", "S", "S"]
                    for dotw in days:
                        yield Label(dotw)
                for _ in range(CalendarDimensions.weeks):
                    with Horizontal(classes="calendar-row"):
                        for _ in range(CalendarDimensions.days):
                            yield Label("", classes="not_current_month")
