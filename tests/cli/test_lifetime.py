"""Who closes the database, and when.

Every command used to close its own session and dispose its own engine on the
last line. `ctx.exit` raises `click.exceptions.Exit`, so on any path that
reported a failure those lines were unreachable -- which is to say the session
and the engine leaked on exactly the invocations where something had already
gone wrong. The same three lines were hand-written at eight sites, so getting it
right anywhere did not get it right anywhere else.

Closing is registered on the Click context now, once, where the database is
opened. `Context.call_on_close` runs on the way out however the command leaves.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import click
import pytest
from click.testing import CliRunner
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import flexi.__main__ as main
import flexi.models.database.engine as database_engine
import flexi.models.database.migrate as database_migrate
import flexi.services.registry as registry_module
from flexi.__main__ import cli
from flexi.locations import database_file
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services.registry import Services, build_services
from flexi.services.settings import parse_settings

type FailurePoint = Literal["services", "sweep", "cache"]


@dataclass(frozen=True, slots=True)
class Preparation:
    failure_at: FailurePoint
    events: list[str]

    def run(self, step: FailurePoint) -> None:
        self.events.append(step)
        if step == self.failure_at:
            msg = f"{step} failed"
            raise RuntimeError(msg)


@dataclass(frozen=True, slots=True)
class DisposableEngine:
    events: list[str]

    def dispose(self) -> None:
        self.events.append("engine disposed")


@dataclass(frozen=True, slots=True)
class ClosableSession:
    events: list[str]

    def close(self) -> None:
        self.events.append("session closed")


@dataclass(frozen=True, slots=True)
class PreparingClock:
    preparation: Preparation

    def sweep(self) -> None:
        self.preparation.run("sweep")


@dataclass(frozen=True, slots=True)
class PreparingHolidays:
    preparation: Preparation

    def fill_if_empty(self) -> None:
        self.preparation.run("cache")


@dataclass(frozen=True, slots=True)
class PreparingRegistry:
    clock: PreparingClock
    bank_holidays: PreparingHolidays


def install_failing_open(
    monkeypatch: pytest.MonkeyPatch, preparation: Preparation
) -> None:
    """Install observable resources and a registry with one failing step."""
    engine = DisposableEngine(preparation.events)
    session = ClosableSession(preparation.events)

    def create_engine(_db_path: Path | None = None) -> DisposableEngine:
        return engine

    def create_session(_engine: object) -> ClosableSession:
        return session

    def build_registry(_session: object) -> PreparingRegistry:
        preparation.run("services")
        return PreparingRegistry(
            clock=PreparingClock(preparation),
            bank_holidays=PreparingHolidays(preparation),
        )

    monkeypatch.setattr(database_migrate, "run_migrations", lambda: None)
    monkeypatch.setattr(database_engine, "create_db_engine", create_engine)
    monkeypatch.setattr(database_engine, "get_session", create_session)
    monkeypatch.setattr(registry_module, "build_services", build_registry)


@pytest.fixture
def home() -> Path:
    """A set-up database under the throwaway XDG home the root conftest makes."""
    db = database_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    run_migrations(db)
    engine = create_db_engine(db)
    session = get_session(engine)
    build_services(session).settings.save_settings(
        parse_settings(
            leave_year_start="04-06",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    session.close()
    engine.dispose()
    return db


def _closings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    closed: list[str] = []
    original = database_engine.database_scope

    @contextmanager
    def watching(
        db_path: Path | None = None,
    ) -> Iterator[tuple[Engine, Session]]:
        try:
            with original(db_path) as database:
                yield database
        finally:
            closed.append("closed")

    monkeypatch.setattr(database_engine, "database_scope", watching)
    return closed


def test_the_database_is_closed_when_a_command_succeeds(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = _closings(monkeypatch)

    result = CliRunner().invoke(cli, ["balance", "show"])

    assert result.exit_code == 0, result.output
    assert closed == ["closed"]


def test_the_database_is_closed_when_a_command_fails(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`clock out` with nothing running reports a failure and exits 1.

    This is the path the hand-written cleanup could never reach.
    """
    closed = _closings(monkeypatch)

    result = CliRunner().invoke(cli, ["clock", "out"])

    assert result.exit_code == 1
    assert "Not clocked in" in result.output
    assert closed == ["closed"], "the failure path leaked the session and the engine"


def test_the_database_is_closed_when_a_command_raises(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing hand-written could have covered this one at all."""
    closed = _closings(monkeypatch)

    def explode(*_args: object, **_kwargs: object) -> None:
        msg = "something went wrong deep inside"
        raise RuntimeError(msg)

    monkeypatch.setattr("flexi.services.ledger.LedgerService.balance", explode)
    result = CliRunner().invoke(cli, ["balance", "show"])

    assert isinstance(result.exception, RuntimeError)
    assert closed == ["closed"]


def test_a_command_uses_one_registry_rather_than_building_a_second(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two graphs means two memo caches, and `invalidate` only clears one.

    Watched at the seam that opens the database, which is also the one that
    hands the registry to the command: `requires_setup` passes it, so there is
    no second lookup that could reach a different one.
    """
    seen: list[Services] = []
    original = main.open_database

    def watching(ctx: click.Context) -> main.Handles:
        handles = original(ctx)
        seen.append(handles.services)
        return handles

    monkeypatch.setattr(main, "open_database", watching)
    CliRunner().invoke(cli, ["balance", "show"])

    assert seen, "the command should open the database"
    assert len({id(services) for services in seen}) == 1


@pytest.mark.parametrize("failure_at", ["services", "sweep", "cache"])
def test_partial_database_open_is_closed(
    failure_at: FailurePoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preparation cannot leak resources acquired before its failing step."""
    events: list[str] = []
    install_failing_open(monkeypatch, Preparation(failure_at, events))

    ctx = click.Context(click.Command("failure"))
    with pytest.raises(RuntimeError, match="failed"), ctx:
        main.open_database(ctx)

    assert events[-2:] == ["session closed", "engine disposed"]
    assert events.count("session closed") == 1
    assert events.count("engine disposed") == 1
