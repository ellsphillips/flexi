"""Resource ownership while the throwaway demonstration is seeded."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Never

import pytest

import flexi.models.database.engine as database_engine
from flexi import __main__ as main
from flexi.models.database.db import Base
from flexi.services import samples


def test_seed_failure_closes_demo_resources(monkeypatch: pytest.MonkeyPatch) -> None:
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

    def fail_to_seed(_session: object, *, anchor: date) -> Never:
        del anchor
        msg = "demo seed failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(database_engine, "create_db_engine", create_engine)
    monkeypatch.setattr(database_engine, "get_session", create_session)
    monkeypatch.setattr(Base.metadata, "create_all", lambda _engine: None)
    monkeypatch.setattr(samples, "seed_demo", fail_to_seed)

    with pytest.raises(RuntimeError, match="demo seed failed"):
        main.run_demo()

    assert events == ["session closed", "engine disposed"]
