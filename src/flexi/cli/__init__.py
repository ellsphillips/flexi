"""Command line surfaces that are too large to sit in __main__.

Each is a plain function taking the service registry and returning an exit
code, so it can be called and asserted on without Click's test runner and
without a subprocess. The decorators in `__main__` are adapters over these.
"""

from __future__ import annotations

import click

from flexi.services.outcome import Outcome


def report(result: Outcome) -> int:
    """Say what happened, and turn it into an exit code.

    Green and zero, or red and one. The same decision the status bar makes in
    the application, so the two surfaces agree about what counts as a failure
    -- and one decision rather than the three copies that were spread across
    two modules, of which only one carried the reason.
    """
    click.secho(result.message, fg="green" if result.success else "red")
    return 0 if result.success else 1
