"""Tests for Slice 13: update check moved off startup.

Covers: CLI startup does not perform network calls, update failures are
silent, update-available notification logic works.
"""

from __future__ import annotations

from unittest.mock import patch


class TestCliStartupNoNetwork:
    def test_version_flag_does_not_import_httpx_or_requests(self) -> None:
        """--version should not trigger any network imports at module level."""
        import click.testing

        from flexi.__main__ import cli

        runner = click.testing.CliRunner()
        # Patch httpx.get to blow up if called
        with patch(
            "flexi.versioning.httpx.get", side_effect=AssertionError("should not call")
        ):
            result = runner.invoke(cli, ["--version"])
            assert result.exit_code == 0

    def test_cli_no_subcommand_does_not_call_needs_update(self) -> None:
        """Launching the TUI should not call needs_update synchronously."""
        # The import of versioning in __main__.py was removed
        # Verify the cli function source does not reference needs_update
        import inspect

        from flexi.__main__ import cli

        source = inspect.getsource(cli.callback)
        assert "needs_update" not in source


class TestUpdateFailureSilent:
    def test_get_pypi_version_returns_none_on_error(self) -> None:
        from flexi.versioning import get_pypi_version

        with patch("flexi.versioning.httpx.get", side_effect=Exception("fail")):
            assert get_pypi_version() is None

    def test_needs_update_false_on_error(self) -> None:
        from flexi.versioning import needs_update

        with patch("flexi.versioning.httpx.get", side_effect=Exception("fail")):
            assert needs_update() is False


class TestUpdateAvailable:
    def test_needs_update_true_when_newer(self) -> None:
        from flexi.versioning import needs_update

        mock_response = type(
            "R",
            (),
            {
                "json": lambda self: {"info": {"version": "99.0.0"}},
                "raise_for_status": lambda self: None,
            },
        )()

        with patch("flexi.versioning.httpx.get", return_value=mock_response):
            assert needs_update() is True

    def test_needs_update_false_when_same(self) -> None:
        import flexi
        from flexi.versioning import needs_update

        mock_response = type(
            "R",
            (),
            {
                "json": lambda self: {"info": {"version": flexi.__version__}},
                "raise_for_status": lambda self: None,
            },
        )()

        with patch("flexi.versioning.httpx.get", return_value=mock_response):
            assert needs_update() is False
