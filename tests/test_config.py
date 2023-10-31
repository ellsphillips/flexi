from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from flexi.config.home import create_basic_user_config


@patch.multiple(Path, exists=lambda _: False, open=mock_open())
def test_initial_creation(capsys: pytest.CaptureFixture[str]) -> None:
    """Check home folder exists before initialising."""
    create_basic_user_config(Path("./projectile"))
    assert not capsys.readouterr().out


def test_subsequent_creation(capsys: pytest.CaptureFixture[str]) -> None:
    """Check home folder exists before initialising."""
    with patch.object(Path, "exists") as mock_exists:
        mock_exists.return_value = True

        with patch.object(Path, "open") as mock_open:
            create_basic_user_config(Path("./projectile"))
            mock_open.assert_called_once()

    assert "Re-inialising flexi..." in capsys.readouterr().out
