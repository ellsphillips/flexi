"""dev script for flexi - run with `uv run textual run --dev ./src/flexi/dev.py`."""

from flexi.app import App
from flexi.models.database.migrate import run_migrations

if __name__ == "__main__":
    run_migrations()
    app = App()
    app.run()
