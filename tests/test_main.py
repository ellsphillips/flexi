import pathlib
import re
from unittest.mock import Mock, patch

import click.testing
import pytest

from flexi.__main__ import flexi
from flexi.constants import Clock
from flexi.log.registrar import JSONRegistrar
from tests.conftest import TEST_LOG


@pytest.fixture()
def runner() -> click.testing.CliRunner:
    """Fixture for invoking command-line interfaces."""
    return click.testing.CliRunner()


def test_flexi_succeeds(runner: click.testing.CliRunner) -> None:
    """It exits with a status code of zero."""
    result = runner.invoke(flexi)
    assert result.exit_code == 0


@pytest.mark.e2e()
def test_flexi_succeeds_in_production_env(
    runner: click.testing.CliRunner,
) -> None:
    """It exits with a status code of zero (end-to-end)."""
    result = runner.invoke(flexi)
    assert result.exit_code == 0


def test_flexi_prints_title(runner: click.testing.CliRunner) -> None:
    """It prints the flexi welcome title."""
    result = runner.invoke(flexi)
    assert result.output.startswith(" flexi ")


def test_flexi_prints_help_given_help_flag(
    runner: click.testing.CliRunner,
) -> None:
    """It prints the help message when given the help flag."""
    result = runner.invoke(flexi, args=["--help"])
    assert "Usage: flexi [OPTIONS] COMMAND [ARGS]..." in result.output


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--version"], re.compile(r"\d+\.\d+\.\d+")),
        (["-v"], re.compile(r"\d+\.\d+\.\d+")),
    ],
)
def test_flexi_prints_version_given_version_flag(
    runner: click.testing.CliRunner,
    args: list[str],
    expected: re.Pattern[str],
) -> None:
    """It prints the version when given the version flag."""
    result = runner.invoke(flexi, args=args)
    assert expected.match(result.output)


@patch("flexi.core.Flexi.initialize", Mock())
def test_init_succeeds(runner: click.testing.CliRunner) -> None:
    """It exits with a status code of zero."""
    result = runner.invoke(flexi, args=["init"])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    ("clock"),
    [Clock.IN, Clock.OUT],
)
@patch("flexi.log.registrar.JSONRegistrar.record_clock", Mock())
def test_clock_succeeds(clock: Clock, runner: click.testing.CliRunner) -> None:
    """It exits with a status code of zero."""
    result = runner.invoke(flexi, args=["clock", clock.value])
    assert result.exit_code == 0


@patch("flexi.__main__.load_log", Mock(return_value=TEST_LOG))
def test_clock_prints_in_action_status(
    runner: click.testing.CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It prints the in action status."""
    monkeypatch.setattr(
        "flexi.log.registrar.JSONRegistrar",
        JSONRegistrar(pathlib.Path.cwd(), TEST_LOG),
    )

    with patch.object(pathlib.Path, "open") as _:
        result = runner.invoke(flexi, args=["clock", "in"])
        assert "clocked  IN  at" in result.output
        result = runner.invoke(flexi, args=["clock", "out"])
        assert "clocked  OUT  at" in result.output
