"""The update check is best-effort and must never delay or break a launch."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import click.testing
import httpx
import pytest

import flexi
from flexi.__main__ import cli
from flexi.versioning import PYPI_URL, available_update, get_pypi_version


class _Response:
    """Just enough of httpx.Response for the two calls versioning makes."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _publishing(version: str) -> _Response:
    return _Response({"info": {"version": version}})


def test_the_cli_never_reaches_the_network() -> None:
    """--version is answered from the installed metadata, not from PyPI."""
    with patch(
        "flexi.versioning.httpx.get", side_effect=AssertionError("reached the network")
    ):
        result = click.testing.CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert flexi.__version__ in result.output


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("no route"),
        httpx.ReadTimeout("too slow"),
        httpx.HTTPStatusError("500", request=None, response=None),  # type: ignore[arg-type]
    ],
)
def test_an_unreachable_index_is_not_an_error(failure: Exception) -> None:
    with patch("flexi.versioning.httpx.get", side_effect=failure):
        assert get_pypi_version() is None
        assert available_update() is None


@pytest.mark.parametrize("payload", [{}, {"info": {}}, {"info": None}, []])
def test_a_malformed_answer_is_not_an_error(payload: Any) -> None:
    """PyPI is a third party; its response shape is not a guarantee."""
    with patch("flexi.versioning.httpx.get", return_value=_Response(payload)):
        assert get_pypi_version() is None


def test_an_unparseable_version_is_not_an_error() -> None:
    with patch("flexi.versioning.httpx.get", return_value=_publishing("not-a-version")):
        assert available_update() is None


def test_a_newer_release_is_reported_by_name() -> None:
    """The caller needs the number to show it, so it comes back rather than True."""
    with patch("flexi.versioning.httpx.get", return_value=_publishing("99.0.0")):
        assert available_update() == "99.0.0"


def test_the_running_version_is_not_an_update() -> None:
    with patch(
        "flexi.versioning.httpx.get", return_value=_publishing(flexi.__version__)
    ):
        assert available_update() is None


def test_an_older_release_is_not_an_update() -> None:
    with patch("flexi.versioning.httpx.get", return_value=_publishing("0.0.1")):
        assert available_update() is None


def test_the_index_is_asked_once_per_check() -> None:
    """The old pair of calls fetched the same document twice on every launch."""
    with patch(
        "flexi.versioning.httpx.get", return_value=_publishing("99.0.0")
    ) as fetch:
        available_update()
    assert fetch.call_count == 1
    assert fetch.call_args.args[0] == PYPI_URL
