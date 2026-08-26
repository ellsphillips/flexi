"""Command line surfaces that are too large to sit in __main__.

Each is a plain function taking the service registry and returning an exit code,
so it can be called and asserted on without Click's test runner.
"""

from __future__ import annotations

import click

from flexi.services.outcome import Outcome


def report(result: Outcome) -> int:
    """Say what happened, and turn it into an exit code.

    Green and zero, or red and one -- the same decision the status bar makes in
    the application, so the two surfaces agree about what counts as a failure.
    That was the stated reason it existed, and it was private to `cli.clock`
    while `cli.balance` spelled the same line out twice.
    """
    click.secho(result.message, fg="green" if result.success else "red")
    return 0 if result.success else 1
