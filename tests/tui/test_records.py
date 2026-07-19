"""Feature 3: a row per day, opening to the day's breakdown."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event

from flexi.components.expandable import DAY, SESSION, ExpandableTable
from flexi.components.modules.records import RecordsModule
from tests.tui.conftest import WIDE, dashboard

pytestmark = pytest.mark.usefixtures("_frozen")


def table(app) -> ExpandableTable:
    return app.screen.query_one("#records-table", ExpandableTable)


async def test_a_week_is_seven_rows_and_a_total(app_factory) -> None:
    """It shows every day in the period, worked or not, plus the period line."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        rows = table(app).visible_rows()
        assert len([row for row in rows if row.kind == DAY]) == 7
        assert rows[-1].key == "t-period"


async def test_space_opens_the_day_under_the_cursor(app_factory) -> None:
    """It reveals the sessions that produced the figures on the row."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key(f"{DAY}2026-06-08")
        await pilot.pause()

        before = len(widget.visible_rows())
        await pilot.press("space")
        await pilot.pause()

        assert len(widget.visible_rows()) > before
        assert any(row.key.startswith(SESSION) for row in widget.visible_rows())


async def test_expanding_does_not_move_the_cursor(app_factory) -> None:
    """It restores the cursor by key, so rows inserted above do not shift it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        target = f"{DAY}2026-06-10"
        widget.focus_key(target)
        await pilot.pause()
        assert widget.cursor_key == target

        widget.toggle(f"{DAY}2026-06-08")  # a row above the cursor
        await pilot.pause()
        assert widget.cursor_key == target


async def test_a_day_with_nothing_recorded_does_not_open(app_factory) -> None:
    """It leaves `space` inert where there is nothing behind the row."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        saturday = f"{DAY}2026-06-13"
        assert widget.toggle(saturday) is False
        assert saturday not in widget.expanded


async def test_shift_space_opens_and_closes_everything(app_factory) -> None:
    """It inverts the majority, so one key always does the visible thing."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.expand_all(expanded=True)
        await pilot.pause()
        assert len(widget.expanded) >= 4

        widget.expand_all()
        await pilot.pause()
        assert widget.expanded == set()


async def test_loading_a_period_costs_the_same_whatever_its_length(app_factory) -> None:
    """It reads a period in a fixed number of queries, not one per day.

    Asserting a *constant* rather than a literal count is the property that
    matters and the one that survives a new lookup being added: the v1 shape
    issued a query per day per concern, so a month cost thirty-one times a week.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        services = app.services
        first = dashboard(app).period.start.replace(day=1)

        def count_queries(days: int) -> int:
            statements: list[str] = []

            def record(conn, cursor, statement, *_args) -> None:  # noqa: ANN001
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append(statement)

            engine = services.session.get_bind()
            event.listen(engine, "before_cursor_execute", record)
            try:
                services.ledger.invalidate()
                services.ledger.days(first, first + timedelta(days=days - 1))
            finally:
                event.remove(engine, "before_cursor_execute", record)
            return len(statements)

        assert count_queries(28) == count_queries(7)


async def test_the_period_total_is_in_the_border_subtitle(app_factory) -> None:
    """It puts the period's figures in the module's live slot."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        subtitle = str(app.screen.query_one(RecordsModule).border_subtitle)
        assert " of " in subtitle
