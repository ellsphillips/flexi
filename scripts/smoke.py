"""Boot the installed package once, against a throwaway database.

Runs against an interpreter carrying Flexi installed from a wheel, with no
source tree on the path.

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
        screen = type(app.screen).__name__
    if app.return_code:
        msg = f"Application exited with status {app.return_code}"
        raise RuntimeError(msg)
    if screen != "SetupScreen":
        msg = f"Expected SetupScreen for a new database, got {screen}"
        raise RuntimeError(msg)
    return screen


def main() -> int:
    # Windows can refuse to delete the database file while the engine holds it.
    with tempfile.TemporaryDirectory() as tmp:
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
