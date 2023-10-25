class DayNotFoundError(Exception):
    """Raised when a day is not found in the flexi log."""

    def __init__(self, date: str) -> None:
        """Initialise the exception."""
        self.date = date
        super().__init__(f"No data record found for {date}")
