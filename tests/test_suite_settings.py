"""The suite's own settings, held to what a CI log needs from them."""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


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
