"""The development entry point.

    uv run textual run --dev scripts/dev.py

Beside `smoke.py` and `shoot.py`, not inside the package: it is a tool for
working on Flexi and the wheel would ship it to every user.

The migration runs first. A developer's database is usually a schema behind the
branch they have just checked out, and migrating after the application is up
surfaces as a SQLAlchemy error about a renamed table several screens in, where
the traceback is about the screen and not about the schema.
"""

from flexi.app import FlexiApp
from flexi.models.database.migrate import run_migrations

if __name__ == "__main__":
    run_migrations()
    app = FlexiApp()
    app.run()
