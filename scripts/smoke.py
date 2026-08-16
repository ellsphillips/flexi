"""Boot the installed package once, against a throwaway database.

Run by CI with an interpreter that has Flexi installed from a wheel and no
source tree on the path. It is the check the test suite cannot make: whether
the artefact people actually download starts.

    python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from flexi.app import FlexiApp
from flexi.models.database.migrate import run_migrations

TERMINAL = (120, 40)


async def _boot(db: Path) -> str:
    app = FlexiApp(db_path=db)
    async with app.run_test(size=TERMINAL) as pilot:
        await pilot.pause()
        return type(app.screen).__name__


def main() -> int:
    # The same `ignore_cleanup_errors` as `flexi --demo`, for the same reason:
    # a bank holiday fetch still finishing as the application closes reopens
    # the database, and Windows will not delete a file that is open. This job
    # answers "does the wheel boot", and it must not go red over a temporary
    # file it could not remove afterwards.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "smoke.db"

        run_migrations(db)
        if not db.exists():
            print("FAIL: migrations left no database", file=sys.stderr)
            return 1

        screen = asyncio.run(_boot(db))
        print(f"booted, showing {screen}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
