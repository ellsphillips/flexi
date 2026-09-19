"""Preferences, and every way the file holding them can be wrong.

`CONFIG` is read at import, before there is a screen to report a failure on:
`BINDINGS` lists read it at class-definition time. So the only outcome a bad
config file may have is a running application on the default keys.
"""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from flexi.config import (
    CONFIG,
    CONFIG_PROBLEM,
    MAXIMUM_MINIMUM_SESSION_SECONDS,
    MAXIMUM_TICK_SECONDS,
    Config,
    Defaults,
    Hotkeys,
    load_config,
    normalise_hotkey,
    read_config,
    section,
)
from flexi.constants import AbsenceType

CLOCK_TOGGLE_C = "hotkeys:\n  clock_toggle: c\n"
"""A valid file, written in whichever encoding the test is about."""


def written(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --- the keys ---------------------------------------------------------------


@pytest.mark.parametrize("kind", list(AbsenceType))
def test_every_absence_type_has_a_book_key(kind: AbsenceType) -> None:
    assert Hotkeys().book(kind) != ""


def test_flexi_books_under_the_toil_key() -> None:
    """The fields follow the displayed name, and `FLEXI` is stored as `flexi`."""
    assert Hotkeys().book(AbsenceType.FLEXI) == Hotkeys().book_toil


def test_hotkey_list_is_canonicalised() -> None:
    assert normalise_hotkey(" ctrl+a, shift+b ") == "ctrl+a,shift+b"
    assert Hotkeys(clock_toggle=" ctrl+a, shift+b ").clock_toggle == ("ctrl+a,shift+b")


@pytest.mark.parametrize("value", ["", "   ", ",", "c,", ",c", "ctrl++c", "c d"])
def test_incomplete_hotkey_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError, match="complete key names"):
        Hotkeys(clock_toggle=value)


@pytest.mark.parametrize("value", ["[/]", "[b]", "[/b]", "[link=x]"])
def test_markup_shaped_hotkey_is_rejected(value: str) -> None:
    """Keys are drawn through Rich markup, where a bracketed value is fatal."""
    with pytest.raises(ValidationError, match="complete key names"):
        Hotkeys(period_cycle=value)


@pytest.mark.parametrize("value", ["/", "[", "]", "!", "f1", "ctrl+l", "ctrl+shift+b"])
def test_key_textual_understands_is_accepted(value: str) -> None:
    """Textual maps a single character to its key name, and the docs use `/`."""
    assert Hotkeys(clock_toggle=value).clock_toggle == value


def test_key_bound_twice_is_rejected() -> None:
    """Textual gives a doubly bound key to one action and says nothing."""
    with pytest.raises(ValidationError, match="bound twice"):
        Hotkeys(book_annual="g")


def test_key_inside_a_list_counts_as_bound() -> None:
    """A comma-separated list is two bindings, not one string to compare."""
    with pytest.raises(ValidationError, match="bound twice"):
        Hotkeys(today="t,g")


def test_double_binding_drops_the_hotkeys_section(tmp_path: Path) -> None:
    path = written(tmp_path / "config.yaml", "hotkeys:\n  book_annual: g\n")

    assert load_config(path).hotkeys == Hotkeys()


def test_malformed_hotkey_section_falls_back(tmp_path: Path) -> None:
    path = written(tmp_path / "config.yaml", "hotkeys:\n  clock_toggle: ','\n")

    assert load_config(path).hotkeys == Hotkeys()


# --- reading the file -------------------------------------------------------


def test_missing_file_gets_the_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "never-written.yaml") == Config()


def test_unparseable_yaml_gets_the_defaults(tmp_path: Path) -> None:
    broken = written(tmp_path / "config.yaml", "hotkeys: [unclosed\n")
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(broken.read_text(encoding="utf-8"))
    assert load_config(broken) == Config()


def test_deeply_nested_file_gets_the_defaults(tmp_path: Path) -> None:
    """A `RecursionError` is not a `YAMLError`, so it needs catching too."""
    deep = written(tmp_path / "config.yaml", "[" * 1000 + "]" * 1000)
    with pytest.raises(RecursionError):
        yaml.safe_load(deep.read_bytes())

    assert load_config(deep) == Config()


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(CLOCK_TOGGLE_C.encode("utf-8-sig"), id="utf-8-with-a-mark"),
        pytest.param(CLOCK_TOGGLE_C.encode("utf-16"), id="utf-16"),
        pytest.param(
            codecs.BOM_UTF16_BE + CLOCK_TOGGLE_C.encode("utf-16-be"),
            id="utf-16-big-endian",
        ),
    ],
)
def test_byte_order_mark_declares_the_encoding(tmp_path: Path, encoded: bytes) -> None:
    """PowerShell's `>` writes UTF-16 with a mark, and PyYAML reads the mark."""
    path = tmp_path / "config.yaml"
    path.write_bytes(encoded)

    assert load_config(path).hotkeys.clock_toggle == "c"


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(CLOCK_TOGGLE_C.encode("utf-16-le"), id="utf-16-with-no-mark"),
        pytest.param(CLOCK_TOGGLE_C.encode("utf-32"), id="utf-32"),
        pytest.param(b"hotkeys:\n  clock_toggle: \xe9\n", id="a-cp1252-byte"),
    ],
)
def test_undeclared_encoding_gets_the_defaults(tmp_path: Path, encoded: bytes) -> None:
    """UTF-32 wears a mark PyYAML reads as UTF-16, so guessing is unsafe."""
    path = tmp_path / "config.yaml"
    path.write_bytes(encoded)

    assert load_config(path) == Config()


@pytest.mark.parametrize("text", ["just a sentence\n", "- one\n- two\n", ""])
def test_non_mapping_file_gets_the_defaults(tmp_path: Path, text: str) -> None:
    """`yaml.safe_load` returns a str, a list and `None` for these three."""
    assert load_config(written(tmp_path / "config.yaml", text)) == Config()


def test_unknown_key_drops_the_whole_section(tmp_path: Path) -> None:
    """A half-applied keymap exists in no file, so the good line goes too."""
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  clock_toggle: c\n  clcok_toggle: x\n",
    )

    assert load_config(path).hotkeys.clock_toggle == Hotkeys().clock_toggle


def test_wrongly_typed_value_gets_the_defaults(tmp_path: Path) -> None:
    """`tick_seconds: soon` is a validation error, not a crash at the first tick."""
    path = written(tmp_path / "config.yaml", "defaults:\n  tick_seconds: soon\n")

    assert load_config(path) == Config()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param("minimum_session_seconds", 0, id="zero-length-session"),
        pytest.param(
            "minimum_session_seconds",
            MAXIMUM_MINIMUM_SESSION_SECONDS,
            id="maximum-session-threshold",
        ),
        pytest.param("tick_seconds", 2, id="positive-tick-interval"),
        pytest.param("tick_seconds", MAXIMUM_TICK_SECONDS, id="maximum-tick-interval"),
    ],
)
def test_timing_boundaries_accept_valid_values(
    tmp_path: Path, name: str, value: int
) -> None:
    """Zero disables the short-session threshold, never the live clock."""
    path = written(tmp_path / "config.yaml", f"defaults:\n  {name}: {value}\n")

    assert getattr(load_config(path).defaults, name) == value


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param("minimum_session_seconds", -1, id="negative-session-threshold"),
        pytest.param(
            "minimum_session_seconds",
            MAXIMUM_MINIMUM_SESSION_SECONDS + 1,
            id="excessive-session-threshold",
        ),
        pytest.param("tick_seconds", 0, id="zero-tick-interval"),
        pytest.param("tick_seconds", -1, id="negative-tick-interval"),
        pytest.param(
            "tick_seconds",
            MAXIMUM_TICK_SECONDS + 1,
            id="excessive-tick-interval",
        ),
    ],
)
def test_invalid_timing_drops_only_its_section(
    tmp_path: Path, name: str, value: int
) -> None:
    path = written(
        tmp_path / "config.yaml",
        f"hotkeys:\n  clock_toggle: c\ndefaults:\n  {name}: {value}\n",
    )

    config = load_config(path)

    assert config.hotkeys.clock_toggle == "c"
    assert config.defaults == Defaults()


@pytest.mark.parametrize(
    "line",
    ["  period: fortnight\n", "  first_day_of_week: 9\n"],
)
def test_default_outside_its_vocabulary_gets_defaults(
    tmp_path: Path, line: str
) -> None:
    """Unvalidated, these reach `DashboardScreen.__init__` and fail there."""
    path = written(tmp_path / "config.yaml", f"defaults:\n{line}")

    assert load_config(path) == Config()


def test_bad_section_spares_the_good_one(tmp_path: Path) -> None:
    """Each section is validated on its own, so one bad key sinks only one."""
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  clock_toggle: c\ndefaults:\n  round_to_minutes: 1\n",
    )

    config = load_config(path)

    assert config.hotkeys.clock_toggle == "c", "the section that read was discarded"
    assert config.defaults == Defaults(), "and the one that did not falls back"


def test_validator_defect_is_not_hidden_as_bad_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback is for invalid preferences, not defects in their validators."""

    def fail(_raw: object) -> Hotkeys:
        message = "validator defect"
        raise RuntimeError(message)

    monkeypatch.setattr(Hotkeys, "model_validate", staticmethod(fail))

    with pytest.raises(RuntimeError, match="validator defect"):
        section(Hotkeys, {})


def test_valid_file_is_honoured_field_by_field(tmp_path: Path) -> None:
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  clock_toggle: c\ndefaults:\n  period: month\n",
    )

    config = load_config(path)

    assert config.hotkeys.clock_toggle == "c"
    assert config.defaults.period == "month"
    assert config.hotkeys.help == Hotkeys().help, "unstated keys keep their defaults"


# --- the loaded config ------------------------------------------------------


def test_module_config_is_resolved_at_import() -> None:
    """Bindings read `CONFIG` while their classes are defined, so it is not lazy."""
    assert isinstance(CONFIG, Config)
    assert CONFIG.hotkeys.clock_toggle != ""


@pytest.mark.parametrize(
    ("target_name", "attribute", "replacement"),
    [
        pytest.param("config", "defaults", Defaults(tick_seconds=2), id="config"),
        pytest.param("hotkeys", "clock_toggle", "c", id="nested-hotkeys"),
        pytest.param("defaults", "tick_seconds", 2, id="nested-defaults"),
    ],
)
def test_loaded_config_is_deeply_immutable(
    tmp_path: Path, target_name: str, attribute: str, replacement: object
) -> None:
    """Shared preferences cannot drift after the bindings have read them."""
    config = load_config(tmp_path / "never-written.yaml")
    targets = {
        "config": config,
        "hotkeys": config.hotkeys,
        "defaults": config.defaults,
    }

    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(targets[target_name], attribute, replacement)


# --- saying that the file was ignored ---------------------------------------


def test_honoured_file_has_nothing_to_report(tmp_path: Path) -> None:
    path = written(tmp_path / "config.yaml", CLOCK_TOGGLE_C)

    config, problem = read_config(path)

    assert config.hotkeys.clock_toggle == "c"
    assert problem == ""


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="an empty file"),
        pytest.param("# nothing but a comment\n", id="comments only"),
    ],
)
def test_file_that_says_nothing_is_not_a_complaint(tmp_path: Path, text: str) -> None:
    path = written(tmp_path / "config.yaml", text)

    assert read_config(path) == (Config(), "")


def test_missing_file_reports_nothing(tmp_path: Path) -> None:
    assert read_config(tmp_path / "never-written.yaml") == (Config(), "")


def test_unreadable_file_is_reported(tmp_path: Path) -> None:
    broken = written(tmp_path / "config.yaml", "hotkeys: [unclosed\n")

    _config, problem = read_config(broken)

    assert str(broken) in problem
    assert "could not read" in problem


def test_non_mapping_file_is_reported(tmp_path: Path) -> None:
    path = written(tmp_path / "config.yaml", "- clock_toggle: c\n")

    _config, problem = read_config(path)

    assert str(path) in problem


def test_dropped_section_names_the_key_that_lost_it(tmp_path: Path) -> None:
    """The section falls back whole, so the key that caused it has to be named."""
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  clock_toggle: c\ndefaults:\n  round_to_minutes: 1\n",
    )

    config, problem = read_config(path)

    assert config.hotkeys.clock_toggle == "c", "the section that read is kept"
    assert "defaults" in problem
    assert "hotkeys" not in problem, "only the section that went is named"
    assert "round_to_minutes" in problem
    assert str(path) in problem


def test_double_binding_is_reported_in_the_validator_words(
    tmp_path: Path,
) -> None:
    """A model-level refusal has no field to name, only a sentence."""
    path = written(tmp_path / "config.yaml", "hotkeys:\n  book_annual: g\n")

    _config, problem = read_config(path)

    assert "hotkeys" in problem
    assert "bound twice" in problem
    assert "Value error" not in problem, "pydantic's own prefix is not for reading"


def test_two_dropped_sections_are_both_named(tmp_path: Path) -> None:
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  nonsense: c\ndefaults:\n  round_to_minutes: 1\n",
    )

    _config, problem = read_config(path)

    assert "hotkeys and defaults" in problem


def test_module_config_and_problem_travel_together() -> None:
    """`CONFIG` and `CONFIG_PROBLEM` come from one read of one file."""
    assert isinstance(CONFIG, Config)
    assert isinstance(CONFIG_PROBLEM, str)
