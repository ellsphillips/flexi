"""Ownership guarantees for the database resource scope."""

from __future__ import annotations

from pathlib import Path
from typing import Never

import pytest

import flexi.models.database.engine as database_engine


def test_engine_is_disposed_when_session_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class DisposableEngine:
        def dispose(self) -> None:
            events.append("engine disposed")

    engine = DisposableEngine()

    def create_engine(_db_path: Path | None = None) -> DisposableEngine:
        return engine

    def fail_to_open_session(_engine: object) -> Never:
        msg = "session construction failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(database_engine, "create_db_engine", create_engine)
    monkeypatch.setattr(database_engine, "get_session", fail_to_open_session)

    with (
        pytest.raises(RuntimeError, match="session construction failed"),
        database_engine.database_scope(),
    ):
        pytest.fail("a scope cannot open without a session")

    assert events == ["engine disposed"]


def test_scope_closes_session_before_disposing_engine_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

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

    def fail_operation() -> Never:
        msg = "operation failed"
        raise RuntimeError(msg)

    with (
        pytest.raises(RuntimeError, match="operation failed"),
        database_engine.database_scope(),
    ):
        fail_operation()

    assert events == ["session closed", "engine disposed"]
