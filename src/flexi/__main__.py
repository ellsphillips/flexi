import click

import flexi
from flexi.components import ui
from flexi.versioning import get_pypi_version, needs_update


@click.group(invoke_without_command=True)
@click.version_option(None, "-v", "--version", message=flexi.__version__)
def cli() -> None:
    """Flexi CLI."""
    ui.welcome()

    if needs_update():
        pypi = get_pypi_version()
        click.secho(
            f"New version available ({flexi.__version__} -> {pypi})! Update with:",
            fg="yellow",
        )
        click.secho(f"\n{' ' * 4} uv tool upgrade flexi", fg="cyan")


if __name__ == "__main__":
    cli()
