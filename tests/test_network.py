"""Resource bounds for metadata received from outside the application."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from threading import Event, Thread
from unittest.mock import Mock, patch

import httpx
import pytest

from flexi import network
from flexi.network import fetch_json

URL = "https://example.test/metadata.json"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[httpx.Client]:
    """Keep platform certificate and proxy setup out of stream-bound tests."""

    def unexpected(request: httpx.Request) -> httpx.Response:
        msg = "each test must supply its own response"
        raise AssertionError(msg)

    client = httpx.Client(transport=httpx.MockTransport(unexpected))
    try:
        monkeypatch.setattr(httpx, "Client", Mock(return_value=client))
        yield client
    finally:
        client.close()


@pytest.fixture
def inline_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake clock tests request deadlines independently of OS scheduling."""

    def worker(*, target: Callable[[], None], daemon: bool) -> Mock:
        return Mock(start=target)

    monkeypatch.setattr(network, "Thread", worker)


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


def test_streaming_request_closes_a_valid_response(client: httpx.Client) -> None:
    stream = Stream(iter([b'{"ok":', b"true}"]))
    with patch.object(client, "send", return_value=response_for(stream)) as send:
        assert fetch_json(URL, timeout=1, budget=2) == {"ok": True}

    request = send.call_args.args[0]
    assert request.headers["Accept-Encoding"] == "identity"
    assert send.call_args.kwargs["stream"] is True
    assert stream.closed.is_set()


def test_oversized_response_stops_before_reading_the_rest(
    monkeypatch: pytest.MonkeyPatch,
    client: httpx.Client,
    inline_worker: None,
) -> None:
    consumed: list[bytes] = []

    def chunks() -> Iterator[bytes]:
        for chunk in (b'"1234', b"56789", b'rest"'):
            consumed.append(chunk)
            yield chunk

    stream = Stream(chunks())
    monkeypatch.setattr(network, "_MAX_RESPONSE_BYTES", 8)
    with patch.object(client, "send", return_value=response_for(stream)):
        assert fetch_json(URL, timeout=1, budget=2) is None

    assert consumed == [b'"1234', b"56789"]
    assert stream.closed.is_set()


def test_compressed_response_is_rejected_before_decompression(
    client: httpx.Client, inline_worker: None
) -> None:
    consumed: list[bool] = []

    def chunks() -> Iterator[bytes]:
        consumed.append(True)
        yield b"untrusted compressed data"

    stream = Stream(chunks())
    with patch.object(
        client, "send", return_value=response_for(stream, encoding="gzip")
    ):
        assert fetch_json(URL, timeout=1, budget=2) is None

    assert consumed == []
    assert stream.closed.is_set()


@pytest.mark.parametrize("expires_during_body", [False, True])
def test_deadline_rejects_a_trickling_or_late_response(
    monkeypatch: pytest.MonkeyPatch,
    client: httpx.Client,
    inline_worker: None,
    expires_during_body: bool,
) -> None:
    now = [0.0]
    monkeypatch.setattr(network, "monotonic", lambda: now[0])
    exhausted = Event()

    def chunks() -> Iterator[bytes]:
        yield b'{"ok":'
        if expires_during_body:
            now[0] = 3.0
        yield b"true}"
        now[0] = 3.0
        exhausted.set()

    stream = Stream(chunks())
    with patch.object(client, "send", return_value=response_for(stream)):
        assert fetch_json(URL, timeout=1, budget=2) is None

    assert exhausted.is_set() is not expires_during_body
    assert stream.closed.is_set()


def test_deadline_returns_while_name_resolution_is_still_waiting(
    client: httpx.Client,
) -> None:
    release = Event()
    started = Event()
    returned = Event()
    results: list[object] = []
    stream = Stream(iter([b'{"ok":true}']))

    def answer(*_args: object, **_kwargs: object) -> httpx.Response:
        started.set()
        release.wait(timeout=10)
        return response_for(stream)

    def fetch() -> None:
        results.append(fetch_json(URL, timeout=1, budget=1))
        returned.set()

    caller = Thread(target=fetch, daemon=True)
    with patch.object(client, "send", side_effect=answer):
        caller.start()
        try:
            assert started.wait(timeout=5)
            assert returned.wait(timeout=3)
            assert results == [None]
            assert not release.is_set()
        finally:
            release.set()
            assert stream.closed.wait(timeout=5)
            caller.join(timeout=5)

    assert not caller.is_alive()


def test_slow_client_setup_does_not_start_a_late_request(
    monkeypatch: pytest.MonkeyPatch,
    client: httpx.Client,
    inline_worker: None,
) -> None:
    now = [0.0]
    monkeypatch.setattr(network, "monotonic", lambda: now[0])

    def build(*_args: object, **_kwargs: object) -> httpx.Request:
        now[0] = 3.0
        return httpx.Request("GET", URL)

    with (
        patch.object(client, "build_request", side_effect=build),
        patch.object(client, "send") as send,
    ):
        assert fetch_json(URL, timeout=1, budget=2) is None

    send.assert_not_called()
