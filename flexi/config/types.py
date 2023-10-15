from dataclasses import dataclass


@dataclass
class Config:
    """Flexi configuration."""

    contract: str
    default_clock_out: str
    new_day_time: str
    week_hours: float
    day_hours: float
