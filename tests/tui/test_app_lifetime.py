"""Database ownership during application construction and teardown."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from threading import Event
from typing import Never

import pytest

import flexi.app as app_module
import flexi.models.database.engine as database_engine
from flexi.app import FlexiApp
from flexi.messages import BankHolidayRefreshCompleted
from tests.database import create_schema


def install_database_spies(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """Replace persistence acquisition with two observable resources."""

    class DisposableEngine:
        def dispose(self) -> None:
            events.append("engine disposed")

    class ClosableSession:
        def close(self) -> None:
            events.append("session closed")

    engine = DisposableEngine()
    session = ClosableSession()

    def create_engine(_db_path: Path | None = None) -> DisposableEngine:
        return engine

    def create_session(_engine: object) -> ClosableSession:
        return session

    monkeypatch.setattr(database_engine, "create_db_engine", create_engine)
    monkeypatch.setattr(database_engine, "get_session", create_session)
    monkeypatch.setattr(app_module, "build_services", lambda _session: object())


def test_late_constructor_failure_closes_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    install_database_spies(monkeypatch, events)

    def fail_to_build_theme() -> Never:
        msg = "application construction failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(app_module, "flexi_theme", fail_to_build_theme)

    with pytest.raises(RuntimeError, match="application construction failed"):
        FlexiApp()

    assert events == ["session closed", "engine disposed"]


def test_application_teardown_owns_each_cleanup_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    install_database_spies(monkeypatch, events)

    app = FlexiApp()
    app.on_unmount()
    app.refresh_holidays()
    app.on_bank_holiday_refresh_completed(BankHolidayRefreshCompleted({}, forced=False))
    app.on_unmount()

    assert events == ["session closed", "engine disposed"]


async def test_blocked_fetch_cannot_retain_the_database(
    tmp_path: Path,
) -> None:
    """The database directory is removable before the network call returns.

    Textual cannot cancel a synchronous request running in a thread, so it may
    outlive the application, but not with a session, a connection or a lease in
    its closure.
    """
    directory = tmp_path / "blocked-refresh"
    directory.mkdir()
    db_path = directory / "flexi.db"
    engine = database_engine.create_db_engine(db_path)
    create_schema(engine)
    engine.dispose()

    started = Event()
    release = Event()
    returned = Event()

    def blocked_fetch() -> object:
        started.set()
        try:
            release.wait()
            return {}
        finally:
            returned.set()

    app = FlexiApp(db_path=db_path, bank_holiday_fetcher=blocked_fetch)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert await asyncio.to_thread(started.wait, 2)

        assert not returned.is_set(), "the regression requires a blocked request"
        shutil.rmtree(directory)
        assert not directory.exists()
    finally:
        release.set()
        assert await asyncio.to_thread(returned.wait, 2)
