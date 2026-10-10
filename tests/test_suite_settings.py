"""The suite's own settings, held to what a CI log needs from them."""

import asyncio
import re
import tomllib
from pathlib import Path

import pytest
import textual.pilot
from hypothesis import settings
from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Static

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


class Measured(Static):
    """Remembers the width it was last told it has."""

    told = 0

    def on_resize(self, event: events.Resize) -> None:
        self.told = event.size.width


class Holder(App[None]):
    def compose(self) -> ComposeResult:
        yield Measured()


async def test_a_pause_ends_after_a_resized_widget_has_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a loaded runner the idle check can pass before the screen is laid out.

    The layout `pause` then performs on its way out posts `Resize` events that
    nothing has read, and a widget that sizes itself on a resize still has the
    old size. That was `test_resizing_the_panel_relays_the_grid_out` on Windows.
    Modelled here by an idle check that yields once and returns.
    """

    async def lagging(min_sleep: float = 0, max_sleep: float = 1) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(textual.pilot, "wait_for_idle", lagging)
    app = Holder()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        widget = app.query_one(Measured)
        widget.styles.width = 42
        await pilot.pause()
        assert widget.told == 42


def test_a_run_repeats_the_order_and_the_examples() -> None:
    """A failure that a second attempt cannot reproduce cannot be told from a flake.

    pytest-randomly picks a new seed on every run unless it is given one, and
    Hypothesis draws new examples unless it is derandomized.
    """
    addopts = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"][
        "ini_options"
    ]["addopts"]
    assert re.search(r"--randomly-seed=\d+\b", addopts)
    assert settings.get_profile("dev").derandomize
    assert settings.get_profile("ci").derandomize


def test_a_stalled_test_shows_its_stack_before_the_timeout_ends_it() -> None:
    """Without the dump, Windows reports an overrun only as a crashed worker.

    pytest-timeout's thread method, the only one Windows has, exits the xdist
    worker, whose stdout xdist discards. The faulthandler dump goes to stderr,
    so it has to fire while the worker is still alive to write it.
    """
    pytest_settings = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"][
        "pytest"
    ]["ini_options"]
    timeout = re.search(r"--timeout=(\d+)", pytest_settings["addopts"])
    assert timeout is not None
    assert 0 < pytest_settings["faulthandler_timeout"] < int(timeout.group(1))
