import os

from flexi.constants import HOME_FOLDER


def check_home_initialised() -> bool:
    return os.path.isdir(HOME_FOLDER)
