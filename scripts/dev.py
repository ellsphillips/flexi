"""The development entry point.

    uv run textual run --dev scripts/dev.py

Beside `smoke.py` and `shoot.py` rather than inside the package, because it is
a tool for working on Flexi and not a part of it -- shipped in the wheel, it was
the one developer script every user installed.

The migration runs first. A developer's database is usually a schema behind the
branch they have just checked out, and migrating after the application is up
surfaces as a SQLAlchemy error about a renamed table several screens in, where
the traceback is about the screen rather than about the schema.
"""

from flexi.app import FlexiApp
from flexi.models.database.migrate import run_migrations

if __name__ == "__main__":
    run_migrations()
    app = FlexiApp()
    app.run()
