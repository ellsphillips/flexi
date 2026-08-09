import getpass
import socket

from rich.text import Text

UNKNOWN = "unknown"


def get_user_host_string() -> Text:
    """Who and where, for the header.

    getpass works through several environment variables and can still find no
    passwd entry at all, which happens in a container often enough to be worth
    surviving. One unknown half should not cost the other.
    """
    try:
        username = getpass.getuser()
    except (OSError, KeyError):
        username = UNKNOWN
    try:
        hostname = socket.gethostname() or UNKNOWN
    except OSError:
        hostname = UNKNOWN
    return Text(f"{username}@{hostname}")
