from dataclasses import dataclass


@dataclass
class MissingHomeError(Exception):
    """
    Exception to raise when the .flexi home directory is missing.

    Attributes:
        message (str): Message displayed when error is raised.
    """

    message: str

    def __str__(self) -> str:
        return self.message
