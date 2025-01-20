"""dev script for flexi - run with `uv run textual run --dev ./src/flexi/dev.py`"""

from flexi.app import App

if __name__ == "__main__":
    app = App()
    app.run()
