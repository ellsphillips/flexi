"""The demo in a real terminal, hung up the way a closed window hangs it up.

The wiring tests stand in for the application, so Textual's threads never exist
there, and a hang-up that leaves one of them running holds the process open.
`pty` does not exist on Windows, and nor does SIGHUP, so this is skipped there.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":  # pragma: no cover - the module is skipped there
    pytest.skip("pty and SIGHUP are POSIX", allow_module_level=True)

import os
import pty
import select
import signal
import subprocess
import time
from pathlib import Path

DEMO = """
import httpx

from flexi.__main__ import cli


def refused(*_args, **_kwargs):
    raise httpx.ConnectError("the test suite does not make network requests")


httpx.Client.send = refused
cli(["--demo"], prog_name="flexi")
"""
"""`flexi --demo`, offline: it asks PyPI for a newer Flexi as it opens."""

ZSH_GAP = 0.004
"""Seconds between the two SIGHUPs zsh's job gets when its window closes."""


def drained(
    controller: int,
    demo: subprocess.Popen[bytes],
    seconds: float,
    until: bytes | None = None,
) -> bytes:
    """What the demo drew, read until ``until`` appears, it ends, or time is up.

    Read all along: a terminal nobody reads fills up, and the demo blocks on it.
    """
    drawn = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not (until and until in drawn):
        ready, _, _ = select.select([controller], [], [], 0.1)
        if not ready:
            if demo.poll() is not None:
                break
            continue
        try:
            chunk = os.read(controller, 65536)
        except OSError:  # Linux: the far end has closed
            break
        if not chunk:
            break
        drawn.extend(chunk)
    return bytes(drawn)


def test_a_demo_hung_up_twice_exits_and_takes_its_records(tmp_path: Path) -> None:
    """The second hang-up must not leave the process running, nor the samples.

    Both come once the demo has settled, as an open window has: among its first
    frames, they did not reproduce the hang.
    """
    controller, terminal = pty.openpty()
    demo = subprocess.Popen(  # noqa: S603 - fixed interpreter and in-repository script
        [sys.executable, "-c", DEMO],
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        start_new_session=True,
    )
    os.close(terminal)
    try:
        assert b"Records" in drained(controller, demo, 60, until=b"Records")
        drained(controller, demo, 1)
        demo.send_signal(signal.SIGHUP)
        time.sleep(ZSH_GAP)
        demo.send_signal(signal.SIGHUP)
        drained(controller, demo, 30)

        # 129 is the handler unwinding. A runner stalled long enough to deliver
        # the second hang-up after the handlers are back sees the default action.
        assert demo.wait(timeout=5) in {128 + signal.SIGHUP, -signal.SIGHUP}
    finally:
        demo.kill()
        demo.wait()
        os.close(controller)
    assert not list(tmp_path.glob("flexi-demo-*"))
