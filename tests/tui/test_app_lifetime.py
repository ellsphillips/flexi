"""Database ownership during application construction and teardown."""

from __future__ import annotations

from pathlib import Path
from typing import Never

import pytest

import flexi.app as app_module
import flexi.models.database.engine as database_engine
from flexi.app import FlexiApp


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
    app.on_unmount()

    assert events == ["session closed", "engine disposed"]
