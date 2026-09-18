"""What the two output streams can take, settled before anything is printed.

Three questions, none of them about what Flexi has to say: whether the console
interprets an escape sequence, whether colour is wanted at all, and whether the
stream can encode the glyphs the figures are drawn with. Answering them once at
the entry point is what keeps every ``click.echo`` in the package a plain call.
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
"""The console mode bit that makes an escape sequence mean something."""

_STANDARD_OUTPUT = -11
_STANDARD_ERROR = -12


def enable_ansi() -> None:
    """Ask both console streams to interpret escape sequences.

    Windows only; a POSIX terminal has read them all along. Click 8.5 dropped
    colorama and nothing else turns the flag on, so a classic conhost window
    prints the escape where the colour should be and stacks a fresh copy of the
    ``flexi init`` menu under every keypress. Textual sets the flag for the
    application and Rich only reads it, which leaves the plain commands as the
    one surface with no path to a working escape sequence.

    A handle that is a pipe or a file has no console mode. That is not a
    failure; there is simply nothing to enable.
    """
    if sys.platform == "win32":  # pragma: no cover - exercised by the Windows job
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        mode = ctypes.c_uint32()
        for standard in (_STANDARD_OUTPUT, _STANDARD_ERROR):
            handle = kernel32.GetStdHandle(standard)
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(
                    handle, mode.value | _VIRTUAL_TERMINAL_PROCESSING
                )


def monochrome() -> bool:
    """True when the environment has asked for no colour.

    ``NO_COLOR`` present and not empty is the rule no-color.org states, and a
    ``dumb`` terminal has said the same thing in the older vocabulary. Rich and
    Textual read both and Click reads neither, so the answer has to be handed
    to Click or the prompts go monochrome while the commands do not.
    """
    return bool(os.environ.get("NO_COLOR")) or (
        os.environ.get("TERM", "").lower() in PLAIN_TERMINALS
    )


def tolerant() -> None:
    """Let a stream that cannot encode a glyph print a replacement for it.

    A redirected stdout on Windows is the ANSI code page, so the U+2212 in
    every delta and the arrow in the leave-year line raise
    ``UnicodeEncodeError`` the moment output is piped to a file. A lost glyph
    is a smaller loss than the line it was in and the exit code with it.

    Only streams nobody is watching. A terminal is left exactly as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and not stream.isatty():
            stream.reconfigure(errors="replace")


def prepare(ctx: click.Context) -> None:
    """Settle what the streams can take, before anything is written to them.

    ``ctx.color`` is inherited by every subcommand context, so setting it once
    on the group is what carries the answer to the ``secho`` calls spread
    across five modules.
    """
    enable_ansi()
    tolerant()
    if monochrome():
        ctx.color = False
