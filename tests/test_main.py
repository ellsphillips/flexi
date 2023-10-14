import click.testing
import pytest

from flexi.__main__ import flexi


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
