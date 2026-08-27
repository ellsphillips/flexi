"""Every service write commits once and recovers its session on failure."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import InvalidRequestError, OperationalError
from sqlalchemy.orm import Session

from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent
from flexi.services.transactions import atomic, write_transaction


def test_atomic_commits_the_enclosed_unit_once(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = Mock(wraps=session.commit)
    rollback = Mock(wraps=session.rollback)
    monkeypatch.setattr(session, "commit", commit)
    monkeypatch.setattr(session, "rollback", rollback)

    with atomic(session):
        pass

    commit.assert_called_once_with()
    rollback.assert_not_called()


def test_atomic_rolls_back_an_enclosed_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("flush failed")
    commit = Mock(wraps=session.commit)
    rollback = Mock(wraps=session.rollback)
    monkeypatch.setattr(session, "commit", commit)
    monkeypatch.setattr(session, "rollback", rollback)

    with pytest.raises(RuntimeError, match="flush failed"), atomic(session):
        raise failure

    commit.assert_not_called()
    rollback.assert_called_once_with()


def test_atomic_rolls_back_a_commit_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("commit failed")
    rollback = Mock(wraps=session.rollback)
    monkeypatch.setattr(session, "commit", Mock(side_effect=failure))
    monkeypatch.setattr(session, "rollback", rollback)

    with pytest.raises(RuntimeError, match="commit failed"), atomic(session):
        pass

    rollback.assert_called_once_with()


def test_write_transaction_refuses_to_discard_pending_changes(session: Session) -> None:
    event = ClockEvent(action=ClockAction.IN, timestamp=datetime(2026, 8, 10, 9))
    session.add(event)

    with (
        pytest.raises(InvalidRequestError, match="no pending changes"),
        write_transaction(session),
    ):
        pass

    assert event in session.new
    session.rollback()


def test_write_transaction_recovers_when_reserving_the_writer_fails(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("reservation failed")
    rollback = Mock(wraps=session.rollback)
    monkeypatch.setattr(session, "connection", Mock(side_effect=failure))
    monkeypatch.setattr(session, "rollback", rollback)

    with (
        pytest.raises(RuntimeError, match="reservation failed"),
        write_transaction(session),
    ):
        pass

    assert rollback.call_count == 2


def test_write_transaction_reserves_sqlites_only_writer(engine: Engine) -> None:
    with Session(engine) as first, Session(engine) as second:
        second.connection().exec_driver_sql("PRAGMA busy_timeout=0")
        event = ClockEvent(action=ClockAction.IN, timestamp=datetime(2026, 8, 10, 9))

        with write_transaction(first):
            second.add(event)
            with pytest.raises(OperationalError, match="database is locked"):
                second.flush()

        second.rollback()
