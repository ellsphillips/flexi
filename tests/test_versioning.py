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
    with patch("httpx.Client.get", side_effect=AssertionError("reached the network")):
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
    with patch("httpx.Client.get", side_effect=failure):
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
def test_a_shell_that_breaks_the_client_is_not_an_error(failure: Exception) -> None:
    """The environment decides what `httpx.Client` raises, before any request.

    `ALL_PROXY=socks5://...` without the socks extra is an `ImportError`, an
    `SSL_CERT_FILE` naming a removed bundle an `OSError`, a proxy URL with a
    bad port an `httpx.InvalidURL` — and none of those is an `HTTPError`. They
    are raised from the constructor, so `Client.get` is never reached and the
    suite's own no-internet seam does not mask them.
    """
    with patch("httpx.Client.__init__", side_effect=failure):
        assert get_pypi_version() is None
        assert available_update() is None


@pytest.mark.parametrize("payload", [{}, {"info": {}}, {"info": None}, []])
def test_a_malformed_answer_is_not_an_error(payload: Any) -> None:
    """PyPI is a third party; its response shape is not a guarantee."""
    with patch("httpx.Client.get", return_value=_Response(payload)):
        assert get_pypi_version() is None


def test_an_unparseable_version_is_not_an_error() -> None:
    with patch("httpx.Client.get", return_value=_publishing("not-a-version")):
        assert available_update() is None


def test_a_newer_release_is_reported_by_name() -> None:
    """The caller needs the number to show it, so it comes back rather than True."""
    with patch("httpx.Client.get", return_value=_publishing("99.0.0")):
        assert available_update() == "99.0.0"


def test_the_reported_version_is_normalised() -> None:
    """The canonical form, not the string PyPI happened to send.

    PEP 440 admits surrounding whitespace, and the header stamps whatever it is
    handed: a newline inside the number is a toast drawn over two lines.
    """
    with patch("httpx.Client.get", return_value=_publishing("\n 99.0.0 \t")):
        assert available_update() == "99.0.0"


def test_the_running_version_is_not_an_update() -> None:
    """The boundary case, and the only test that covers it.

    This patched `flexi.versioning.httpx.get`, which `get_pypi_version` stopped
    using when it moved to a `Client` -- the seam the other six tests in this
    file already use. So the patch caught nothing, the autouse no-internet
    fixture refused the real connection, and the `None` being asserted was "PyPI
    could not be read" rather than "the published version is this one".

    It passed either way, which meant `>` could become `>=` and stay green: the
    application would then offer an update to the version already running, on
    every launch, and nothing here would have said so.
    """
    with patch("httpx.Client.get", return_value=_publishing(flexi.__version__)):
        assert available_update() is None


def test_an_older_release_is_not_an_update() -> None:
    with patch("httpx.Client.get", return_value=_publishing("0.0.1")):
        assert available_update() is None


def test_the_index_is_asked_once_per_check() -> None:
    """The old pair of calls fetched the same document twice on every launch."""
    with patch("httpx.Client.get", return_value=_publishing("99.0.0")) as fetch:
        available_update()
    assert fetch.call_count == 1
    assert fetch.call_args.args[0] == PYPI_URL
