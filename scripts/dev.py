"""The development entry point.

    uv run textual run --dev scripts/dev.py

Sits outside the package, beside `smoke.py` and `shoot.py`, so the wheel does
not ship it. Migrations run before the application starts, so a schema a branch
behind fails here and not as a SQLAlchemy error several screens in.
"""

from flexi.app import FlexiApp
from flexi.models.database.migrate import run_migrations

if __name__ == "__main__":
    run_migrations()
    app = FlexiApp()
    app.run()
