import pytest

from flexi.__main__ import flexi
from flexi.utils import print_help_msg


def test_print_help_msg(capsys: pytest.CaptureFixture[str]) -> None:
    """It prints the help message for a given command."""
    print_help_msg(flexi)
    captured = capsys.readouterr()
    assert captured.out.startswith("Usage:  [OPTIONS] COMMAND [ARGS]...")
