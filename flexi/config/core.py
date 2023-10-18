from pathlib import Path

from flexi.config.types import Config

HOME_DIR = Path.home() / ".flexi/"

DEFAULT_CONFIG = Config(
    contract="full_time",
    default_clock_out="17:00:00",
    new_day_time="00:00:00",
    week_hours=37.0,
    day_hours=7.4,
)
