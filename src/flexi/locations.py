from pathlib import Path

from xdg_base_dirs import xdg_config_home, xdg_data_home


class FilePaths:
    APP_DIRECTORY = "flexi"
    CONFIG = "config.yaml"
    DATABASE = "db.db"


STATIC_DIRECTORY = Path(__file__).parent / "static"


def app_directory(root: Path) -> Path:
    """Create and return the application directory."""
    directory = root / FilePaths.APP_DIRECTORY
    directory.mkdir(exist_ok=True, parents=True)
    return directory


def data_directory() -> Path:
    return app_directory(xdg_data_home())


def config_directory() -> Path:
    return app_directory(xdg_config_home())


def config_file() -> Path:
    return config_directory() / FilePaths.CONFIG


def database_file() -> Path:
    return data_directory() / FilePaths.DATABASE
