import re

import click.testing
import pytest

from flexi.__main__ import flexi
from flexi.constants import Clock


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


def test_clock_succeeds(runner: click.testing.CliRunner) -> None:
    """It exits with a status code of zero."""
    result = runner.invoke(flexi, args=["clock", "in"])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["clock", "in"], Clock.IN),
        (["clock", "out"], Clock.OUT),
    ],
)
def test_clock_prints_action_status(
    runner: click.testing.CliRunner, args: list[str], expected: Clock
) -> None:
    """It exits with a status code of zero."""
    result = runner.invoke(flexi, args=args)
    assert f" {expected.name} " in result.output


def test_init_succeeds(runner: click.testing.CliRunner) -> None:
    """It exits with a status code of zero."""
    result = runner.invoke(flexi, args=["init"])
    assert result.exit_code == 0
