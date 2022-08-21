from dataclasses import dataclass


@dataclass
class InvalidConfigFileError(Exception):
    """
    Exception to raise when the provided config file is invalid or missing.
    Attributes:
        message (str): Message displayed when error is raised.
    """

    message: str

    def __str__(self) -> str:
        return self.message
