"""Bounded JSON reads for optional external metadata."""

from __future__ import annotations

import json
from contextlib import closing
from threading import Thread
from time import monotonic

__all__ = ("fetch_json",)

_MAX_RESPONSE_BYTES = 1024 * 1024


def fetch_json(url: str, *, timeout: float, budget: float) -> object | None:
    """Fetch at most 1 MiB of JSON within a wall-clock budget.

    HTTPX's phase timeouts do not bound DNS resolution or a response that keeps
    trickling bytes. A daemon bounds the caller's wait; checking the deadline
    between chunks also stops a late response from downloading indefinitely.
    Every failure means optional metadata is unavailable to the caller.
    """
    import httpx

    deadline = monotonic() + budget
    fetched: list[object] = []

    def request() -> None:
        try:
            with httpx.Client(timeout=timeout) as client:
                outgoing = client.build_request(
                    "GET", url, headers={"Accept-Encoding": "identity"}
                )
                if monotonic() >= deadline:
                    return
                with closing(client.send(outgoing, stream=True)) as response:
                    response.raise_for_status()
                    # Optional metadata does not need compression. Refusing a
                    # server that ignores identity also prevents decompression
                    # from allocating beyond the response limit in one chunk.
                    encoding = response.headers.get("Content-Encoding", "identity")
                    if encoding.strip().lower() not in {"", "identity"}:
                        return
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if (
                            monotonic() >= deadline
                            or len(body) + len(chunk) > _MAX_RESPONSE_BYTES
                        ):
                            return
                        body.extend(chunk)
                    payload: object = json.loads(body)
                    if monotonic() < deadline:
                        fetched.append(payload)
        except Exception:  # noqa: BLE001 - optional requests fail closed
            return

    worker = Thread(target=request, daemon=True)
    worker.start()
    worker.join(budget)
    return fetched[0] if fetched else None
