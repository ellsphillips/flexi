import re

import click.testing
import pytest

from flexi.__main__ import cli


@pytest.fixture(name="runner")
def runner() -> click.testing.CliRunner:
    """Fixture for invoking command-line interfaces."""
    return click.testing.CliRunner()


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
    result = runner.invoke(cli, args=args)
    assert expected.match(result.output)
