"""Every service write commits once and recovers its session on failure."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from flexi.services.transactions import atomic


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
