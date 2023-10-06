import click.testing
import pytest

from flexi.__main__ import main


@pytest.fixture()
def runner() -> click.testing.CliRunner:
    """Fixture for invoking command-line interfaces."""
    return click.testing.CliRunner()


def test_main_succeeds(runner: click.testing.CliRunner) -> None:
    """It exits with a status code of zero."""
    result = runner.invoke(main)
    assert result.exit_code == 0


@pytest.mark.e2e()
def test_main_succeeds_in_production_env(runner: click.testing.CliRunner) -> None:
    """It exits with a status code of zero (end-to-end)."""
    result = runner.invoke(main)
    assert result.exit_code == 0


def test_main_prints_title(runner: click.testing.CliRunner) -> None:
    """It prints the flexi welcome title."""
    result = runner.invoke(main)
    assert "flexi  v" in result.output
