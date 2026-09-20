"""Stream capability, settled before anything is printed.

Whether the console interprets an escape sequence, whether colour is wanted,
and whether the stream can encode the glyphs the figures are drawn with are
settled once at the entry point, so every ``click.echo`` in the package stays a
plain call.
"""

from __future__ import annotations

import io
import os
import sys

import click

__all__ = ("PLAIN_TERMINALS", "enable_ansi", "monochrome", "prepare", "tolerant")

PLAIN_TERMINALS = frozenset({"dumb", "unknown"})
"""``TERM`` values that mean the terminal draws text and nothing else."""


_VIRTUAL_TERMINAL_PROCESSING = 0x0004
"""Console mode bit that makes a console interpret escape sequences."""

_STANDARD_OUTPUT = -11
_STANDARD_ERROR = -12


def enable_ansi() -> None:
    """Ask both console streams to interpret escape sequences.

    Windows only; a POSIX terminal already does. Click 8.5 dropped colorama,
    Textual sets the flag for the application alone and Rich only reads it, so
    the plain commands have to set it themselves. A handle that is a pipe or a
    file has no console mode, and nothing is enabled for it.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        mode = wintypes.DWORD()
        for standard in (_STANDARD_OUTPUT, _STANDARD_ERROR):
            handle = kernel32.GetStdHandle(standard)
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(
                    handle, mode.value | _VIRTUAL_TERMINAL_PROCESSING
                )


def monochrome() -> bool:
    """True when the environment has asked for no colour.

    ``NO_COLOR`` present and not empty is the rule no-color.org states, and a
    ``dumb`` terminal says the same in the older vocabulary. Rich and Textual
    read both and Click reads neither, so the answer is handed to Click.
    """
    return bool(os.environ.get("NO_COLOR")) or (
        os.environ.get("TERM", "").lower() in PLAIN_TERMINALS
    )


def tolerant() -> None:
    """Let a stream that cannot encode a glyph print a replacement for it.

    A redirected stdout on Windows is the ANSI code page, so the U+2212 in
    every delta and the arrow in the leave-year line raise
    ``UnicodeEncodeError`` once output is piped to a file. Only redirected
    streams are reconfigured; a terminal is left as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and not stream.isatty():
            stream.reconfigure(errors="replace")


def prepare(ctx: click.Context) -> None:
    """Settle what the streams can take, before anything is written to them.

    ``ctx.color`` is inherited by every subcommand context, so setting it on
    the group carries the answer to every ``secho`` call in the package.
    """
    enable_ansi()
    tolerant()
    if monochrome():
        ctx.color = False
