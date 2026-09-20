"""Resource bounds for metadata received from outside the application."""

from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from unittest.mock import patch

import httpx
import pytest

from flexi import network
from flexi.network import fetch_json

URL = "https://example.test/metadata.json"


class Stream(httpx.SyncByteStream):
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self.chunks = chunks
        self.closed = Event()

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks

    def close(self) -> None:
        self.closed.set()


def response_for(stream: Stream, *, encoding: str = "identity") -> httpx.Response:
    return httpx.Response(
        200,
        stream=stream,
        headers={"Content-Encoding": encoding},
        request=httpx.Request("GET", URL),
    )


def test_streaming_request_closes_a_valid_response() -> None:
    stream = Stream(iter([b'{"ok":', b"true}"]))
    with patch("httpx.Client.send", return_value=response_for(stream)) as send:
        assert fetch_json(URL, timeout=1, budget=2) == {"ok": True}

    request = send.call_args.args[0]
    assert request.headers["Accept-Encoding"] == "identity"
    assert send.call_args.kwargs["stream"] is True
    assert stream.closed.is_set()


def test_oversized_response_stops_before_reading_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumed: list[bytes] = []

    def chunks() -> Iterator[bytes]:
        for chunk in (b'"1234', b"56789", b'rest"'):
            consumed.append(chunk)
            yield chunk

    stream = Stream(chunks())
    monkeypatch.setattr(network, "_MAX_RESPONSE_BYTES", 8)
    with patch("httpx.Client.send", return_value=response_for(stream)):
        assert fetch_json(URL, timeout=1, budget=2) is None

    assert consumed == [b'"1234', b"56789"]
    assert stream.closed.is_set()


def test_compressed_response_is_rejected_before_decompression() -> None:
    consumed: list[bool] = []

    def chunks() -> Iterator[bytes]:
        consumed.append(True)
        yield b"untrusted compressed data"

    stream = Stream(chunks())
    with patch("httpx.Client.send", return_value=response_for(stream, encoding="gzip")):
        assert fetch_json(URL, timeout=1, budget=2) is None

    assert consumed == []
    assert stream.closed.is_set()


@pytest.mark.parametrize("expires_during_body", [False, True])
def test_deadline_rejects_a_trickling_or_late_response(
    monkeypatch: pytest.MonkeyPatch, expires_during_body: bool
) -> None:
    now = [0.0]
    monkeypatch.setattr(network, "monotonic", lambda: now[0])

    def chunks() -> Iterator[bytes]:
        yield b'{"ok":'
        if expires_during_body:
            now[0] = 3.0
        yield b"true}"
        now[0] = 3.0

    stream = Stream(chunks())
    with patch("httpx.Client.send", return_value=response_for(stream)):
        assert fetch_json(URL, timeout=1, budget=2) is None

    assert stream.closed.is_set()


def test_deadline_returns_while_name_resolution_is_still_waiting() -> None:
    release = Event()
    started = Event()
    stream = Stream(iter([b'{"ok":true}']))

    def answer(*_args: object, **_kwargs: object) -> httpx.Response:
        started.set()
        release.wait(timeout=5)
        return response_for(stream)

    with patch("httpx.Client.send", side_effect=answer):
        try:
            assert fetch_json(URL, timeout=1, budget=1) is None
            assert started.is_set()
            assert not release.is_set()
        finally:
            release.set()
            assert stream.closed.wait(timeout=5)


def test_slow_client_setup_does_not_start_a_late_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(network, "monotonic", lambda: now[0])

    def build(*_args: object, **_kwargs: object) -> httpx.Request:
        now[0] = 3.0
        return httpx.Request("GET", URL)

    with (
        patch("httpx.Client.build_request", side_effect=build),
        patch("httpx.Client.send") as send,
    ):
        assert fetch_json(URL, timeout=1, budget=2) is None

    send.assert_not_called()
