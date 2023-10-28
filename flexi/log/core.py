import json
from pathlib import Path

from flexi.log.model import Log


def load_log(path: Path) -> Log:
    """Load the log file."""
    with Path.open(path, encoding="utf-8") as f:
        data = json.load(f)
        return Log(days=data)
