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


def _response(payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("GET", PYPI_URL))


def _publishing(version: str) -> httpx.Response:
    return _response({"info": {"version": version}})


def test_cli_never_reaches_the_network() -> None:
    """--version is answered from the installed metadata, not from PyPI."""
    with patch("httpx.Client.send", side_effect=AssertionError("reached the network")):
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
def test_unreachable_index_is_not_an_error(failure: Exception) -> None:
    with patch("httpx.Client.send", side_effect=failure):
        assert get_pypi_version() is None
        assert available_update() is None


@pytest.mark.parametrize(
    "failure",
    [
        ImportError("Using SOCKS proxy, but the 'socksio' package is not installed"),
        FileNotFoundError(2, "No such file or directory"),
        IsADirectoryError(21, "Is a directory"),
        httpx.InvalidURL("Invalid port: 'abc'"),
    ],
    ids=["socks-proxy", "missing-ca-file", "ca-file-is-a-directory", "bad-proxy-url"],
)
def test_broken_client_is_not_an_error(failure: Exception) -> None:
    """The environment decides what `httpx.Client` raises, before any request.

    `ALL_PROXY` without the socks extra raises `ImportError`, an `SSL_CERT_FILE`
    naming a removed bundle `OSError`, a bad proxy port `httpx.InvalidURL` —
    none of them an `HTTPError`, and all of them from the constructor.
    """
    with patch("httpx.Client.__init__", side_effect=failure):
        assert get_pypi_version() is None
        assert available_update() is None


@pytest.mark.parametrize(
    "payload", [{}, {"info": {}}, {"info": None}, [], {"info": {"version": 99}}]
)
def test_malformed_answer_is_not_an_error(payload: Any) -> None:
    """PyPI is a third party; its response shape is not a guarantee."""
    with patch("httpx.Client.send", return_value=_response(payload)):
        assert get_pypi_version() is None


def test_unparseable_version_is_not_an_error() -> None:
    with patch("httpx.Client.send", return_value=_publishing("not-a-version")):
        assert available_update() is None


def test_newer_release_is_reported_by_name() -> None:
    """The caller needs the number to show it."""
    with patch("httpx.Client.send", return_value=_publishing("99.0.0")):
        assert available_update() == "99.0.0"


def test_reported_version_is_normalised() -> None:
    """PEP 440 admits surrounding whitespace; the header stamps what it is given."""
    with patch("httpx.Client.send", return_value=_publishing("\n 99.0.0 \t")):
        assert available_update() == "99.0.0"


def test_running_version_is_not_an_update() -> None:
    """The boundary: an update is offered on `>`, so the running version is not."""
    with patch("httpx.Client.send", return_value=_publishing(flexi.__version__)):
        assert available_update() is None


def test_older_release_is_not_an_update() -> None:
    with patch("httpx.Client.send", return_value=_publishing("0.0.1")):
        assert available_update() is None


def test_index_is_asked_once_per_check() -> None:
    with patch("httpx.Client.send", return_value=_publishing("99.0.0")) as fetch:
        available_update()
    assert fetch.call_count == 1
    assert str(fetch.call_args.args[0].url) == PYPI_URL
    assert fetch.call_args.kwargs["stream"] is True
