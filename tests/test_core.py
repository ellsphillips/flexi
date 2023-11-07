from pathlib import Path
from unittest.mock import mock_open, patch

from flexi.core import Flexi


def test_flexi_succeeds() -> None:
    """It exits with a status code of zero."""
    with patch.multiple(Path, exists=lambda _: True, open=mock_open()):
        flexi = Flexi()
        flexi.initialize(Path.cwd())
