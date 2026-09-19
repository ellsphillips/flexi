"""Preferences, and every way the file holding them can be wrong.

Config is read once, at import, before Flexi can draw anything to say that it
failed -- `BINDINGS` lists read `CONFIG` at class-definition time. So the only
acceptable outcome of a bad config file is a running application on the default
keys: a typo in a keybinding must never be the reason somebody cannot open
their own time records.
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
"""A valid file, spelled in whichever encoding a test is about."""


def written(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# -- the keys ----------------------------------------------------------------


@pytest.mark.parametrize("kind", list(AbsenceType))
def test_every_absence_type_names_the_key_that_books_it(kind: AbsenceType) -> None:
    """Derived from the type, so a legend cannot disagree with the binding.

    A missing field would be an `AttributeError` on the booking path with mypy
    clean and the suite green, which is exactly the failure `book` exists to
    make impossible.
    """
    assert Hotkeys().book(kind) != ""


def test_toil_is_booked_under_the_name_it_is_shown_by() -> None:
    """`flexi` is the stored value and `toil` is the displayed one.

    The fields follow the display token, so the one type whose two names differ
    is the one worth pinning: reading the stored value would ask for
    `book_flexi`, which does not exist.
    """
    assert Hotkeys().book(AbsenceType.FLEXI) == Hotkeys().book_toil


def test_a_list_of_hotkeys_is_canonicalised_before_textual_reads_it() -> None:
    assert normalise_hotkey(" ctrl+a, shift+b ") == "ctrl+a,shift+b"
    assert Hotkeys(clock_toggle=" ctrl+a, shift+b ").clock_toggle == ("ctrl+a,shift+b")


@pytest.mark.parametrize("value", ["", "   ", ",", "c,", ",c", "ctrl++c", "c d"])
def test_an_incomplete_hotkey_is_rejected_before_app_import(value: str) -> None:
    with pytest.raises(ValidationError, match="complete key names"):
        Hotkeys(clock_toggle=value)


@pytest.mark.parametrize("value", ["[/]", "[b]", "[/b]", "[link=x]"])
def test_a_hotkey_shaped_like_markup_is_rejected(value: str) -> None:
    """Every key is drawn through Rich markup, and `[/]` closes nothing.

    `period_cycle` goes in the month view's border subtitle, so a bracketed
    value is a `MarkupError` before the dashboard draws; every other key goes
    in a `Static` on the help screen, so it is a `MarkupError` the moment `?`
    is pressed. Either way the file is valid and the application is gone.
    """
    with pytest.raises(ValidationError, match="complete key names"):
        Hotkeys(period_cycle=value)


@pytest.mark.parametrize("value", ["/", "[", "]", "!", "f1", "ctrl+l", "ctrl+shift+b"])
def test_a_key_textual_understands_is_accepted(value: str) -> None:
    """Textual maps a single character to its key name, and the docs use `/`."""
    assert Hotkeys(clock_toggle=value).clock_toggle == value


def test_one_key_may_only_answer_to_one_action() -> None:
    """`book_annual: g` against the default `go_to_date: g` leaves A dead.

    Textual gives the key to one of them and says nothing about the other, and
    the help screen offers no clue which one won.
    """
    with pytest.raises(ValidationError, match="bound twice"):
        Hotkeys(book_annual="g")


def test_a_key_listed_beside_another_counts_as_bound() -> None:
    """A comma-separated list is two bindings, not one string to compare."""
    with pytest.raises(ValidationError, match="bound twice"):
        Hotkeys(today="t,g")


def test_a_hotkey_section_that_binds_a_key_twice_falls_back(tmp_path: Path) -> None:
    """The whole section goes, as it does for a misspelled field name.

    A keymap with one dead binding in it exists in no file and cannot be
    recovered by fixing the line that caused it.
    """
    path = written(tmp_path / "config.yaml", "hotkeys:\n  book_annual: g\n")

    assert load_config(path).hotkeys == Hotkeys()


def test_a_malformed_hotkey_section_falls_back_to_safe_defaults(
    tmp_path: Path,
) -> None:
    path = written(tmp_path / "config.yaml", "hotkeys:\n  clock_toggle: ','\n")

    assert load_config(path).hotkeys == Hotkeys()


# -- reading the file --------------------------------------------------------


def test_a_machine_with_no_config_file_gets_the_defaults(tmp_path: Path) -> None:
    """The first run has nothing to read, and is the commonest run of all."""
    assert load_config(tmp_path / "never-written.yaml") == Config()


def test_a_file_that_is_not_yaml_at_all_gets_the_defaults(tmp_path: Path) -> None:
    """A half-typed file is a parse error, not a reason to refuse to start."""
    broken = written(tmp_path / "config.yaml", "hotkeys: [unclosed\n")
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(broken.read_text(encoding="utf-8"))
    assert load_config(broken) == Config()


def test_a_file_nested_too_deep_to_parse_gets_the_defaults(tmp_path: Path) -> None:
    """A stack overflow is not a `YAMLError`, and `CONFIG` is read at import.

    Uncaught it is a raw `RecursionError` traceback out of `flexi clock in`,
    `flexi balance` and the dashboard alike, from a file the docstring promises
    yields the defaults.
    """
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
def test_a_file_that_declares_its_encoding_is_read_in_it(
    tmp_path: Path, encoded: bytes
) -> None:
    """PowerShell's `>` writes UTF-16 with a byte order mark without being asked.

    Decoded as UTF-8 that file is a `UnicodeDecodeError` and every line in it
    is discarded, so a Windows user's preferences are ignored in silence and
    each edit changes nothing. A mark is a declaration, and PyYAML reads it
    when it is handed the bytes.
    """
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
def test_a_file_in_an_undeclared_encoding_gets_the_defaults(
    tmp_path: Path, encoded: bytes
) -> None:
    """Undeclared is unreadable: UTF-32 wears a mark PyYAML reads as UTF-16.

    Guessing is worse than falling back. A keybinding mis-decoded into a
    character nobody can type reaches Textual as a real binding, where the
    defaults leave the file for its author to fix.
    """
    path = tmp_path / "config.yaml"
    path.write_bytes(encoded)

    assert load_config(path) == Config()


@pytest.mark.parametrize("text", ["just a sentence\n", "- one\n- two\n", ""])
def test_a_file_that_is_not_a_mapping_gets_the_defaults(
    tmp_path: Path, text: str
) -> None:
    """A list, a bare string and an empty file all parse without being a config.

    `yaml.safe_load` returns a str, a list and `None` respectively, and handing
    any of them to pydantic is a different exception in each case.
    """
    assert load_config(written(tmp_path / "config.yaml", text)) == Config()


def test_a_key_the_file_invents_falls_back_whole_rather_than_in_part(
    tmp_path: Path,
) -> None:
    """One misspelling must not leave a half-applied keymap.

    `extra="forbid"` rejects the section, and the fallback is the whole of that
    section -- so the good line beside the bad one is discarded with it.
    Applying it would give a keymap that exists in no file and cannot be
    reproduced by fixing the typo. The *other* section still reads; that is
    what `test_a_bad_section_does_not_take_the_good_one_with_it` pins.
    """
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  clock_toggle: c\n  clcok_toggle: x\n",
    )

    assert load_config(path).hotkeys.clock_toggle == Hotkeys().clock_toggle


def test_a_value_of_the_wrong_shape_gets_the_defaults(tmp_path: Path) -> None:
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
    """Zero disables only the short-session threshold, never the live clock."""
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
def test_invalid_timing_boundaries_fall_back_only_their_section(
    tmp_path: Path, name: str, value: int
) -> None:
    """Invalid intervals cannot reach Textual or discard every real session."""
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
def test_a_default_outside_its_vocabulary_gets_the_defaults(
    tmp_path: Path, line: str
) -> None:
    """Both were bare enough to reach the screens and fail there instead.

    `period` was a `str`, so `fortnight` sailed through validation and became a
    `ValueError` inside `DashboardScreen.__init__` -- a preference typo taking
    the application down while it built its first screen. `first_day_of_week`
    was an unbounded `int`, and a 9 rotated the calendar grid by `9 % 7` while
    the column headings, sliced rather than rotated, silently stayed on Monday.
    """
    path = written(tmp_path / "config.yaml", f"defaults:\n{line}")

    assert load_config(path) == Config()


def test_a_bad_section_does_not_take_the_good_one_with_it(tmp_path: Path) -> None:
    """`extra="forbid"` makes an unknown key an error, and the file was one unit.

    So a single unknown key under `defaults` threw the hotkeys away too, with
    nothing said — and the example in `docs/ARCHITECTURE.md` contained two of
    them, so somebody who copied the documented config got every default back
    and no way to tell why.
    """
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  clock_toggle: c\ndefaults:\n  round_to_minutes: 1\n",
    )

    config = load_config(path)

    assert config.hotkeys.clock_toggle == "c", "the section that read was discarded"
    assert config.defaults == Defaults(), "and the one that did not falls back"


def test_an_unexpected_validator_failure_is_not_hidden_as_bad_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback is for invalid preferences, not defects in their validators."""

    def fail(_raw: object) -> Hotkeys:
        message = "validator defect"
        raise RuntimeError(message)

    monkeypatch.setattr(Hotkeys, "model_validate", staticmethod(fail))

    with pytest.raises(RuntimeError, match="validator defect"):
        section(Hotkeys, {})


def test_what_the_file_says_is_what_is_used(tmp_path: Path) -> None:
    """The other half of the bargain: a valid file is honoured, field by field."""
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  clock_toggle: c\ndefaults:\n  period: month\n",
    )

    config = load_config(path)

    assert config.hotkeys.clock_toggle == "c"
    assert config.defaults.period == "month"
    assert config.hotkeys.help == Hotkeys().help, "unstated keys keep their defaults"


# -- the loaded config -------------------------------------------------------


def test_the_module_level_config_is_the_one_the_bindings_read() -> None:
    """`CONFIG` is resolved at import, so it is a real config and not `None`.

    Bindings read it while their classes are being defined; a lazily loaded one
    would be an `AttributeError` at import time of the first widget.
    """
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
    """Shared module preferences cannot drift after bindings have read them."""
    config = load_config(tmp_path / "never-written.yaml")
    targets = {
        "config": config,
        "hotkeys": config.hotkeys,
        "defaults": config.defaults,
    }

    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(targets[target_name], attribute, replacement)


# -- saying that the file was ignored ----------------------------------------


def test_a_file_taken_as_written_has_nothing_to_report(tmp_path: Path) -> None:
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
def test_a_file_that_says_nothing_is_not_a_complaint(tmp_path: Path, text: str) -> None:
    """Silence is not a mistake, and reporting it would cry wolf every run."""
    path = written(tmp_path / "config.yaml", text)

    assert read_config(path) == (Config(), "")


def test_a_machine_with_no_config_file_reports_nothing(tmp_path: Path) -> None:
    """The commonest run of all. Nothing was ignored, because nothing was said."""
    assert read_config(tmp_path / "never-written.yaml") == (Config(), "")


def test_an_unreadable_file_says_none_of_it_is_in_force(tmp_path: Path) -> None:
    broken = written(tmp_path / "config.yaml", "hotkeys: [unclosed\n")

    _config, problem = read_config(broken)

    assert str(broken) in problem
    assert "could not read" in problem


def test_a_file_that_is_not_a_mapping_says_so_too(tmp_path: Path) -> None:
    path = written(tmp_path / "config.yaml", "- clock_toggle: c\n")

    _config, problem = read_config(path)

    assert str(path) in problem


def test_a_dropped_section_names_itself_and_the_line_that_lost_it(
    tmp_path: Path,
) -> None:
    """The section falls back whole, so the key that caused it has to be named.

    Without it the file sits there looking as though it is in force, and the
    period somebody chose to open on has gone with the key they misspelled.
    """
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


def test_a_key_bound_twice_is_reported_in_the_words_the_validator_used(
    tmp_path: Path,
) -> None:
    """A model-level refusal has no field to name, only a sentence."""
    path = written(tmp_path / "config.yaml", "hotkeys:\n  book_annual: g\n")

    _config, problem = read_config(path)

    assert "hotkeys" in problem
    assert "bound twice" in problem
    assert "Value error" not in problem, "pydantic's own prefix is not for reading"


def test_both_sections_going_names_both(tmp_path: Path) -> None:
    path = written(
        tmp_path / "config.yaml",
        "hotkeys:\n  nonsense: c\ndefaults:\n  round_to_minutes: 1\n",
    )

    _config, problem = read_config(path)

    assert "hotkeys and defaults" in problem


def test_the_module_level_pair_travels_together() -> None:
    """`CONFIG` and `CONFIG_PROBLEM` come from one read of one file.

    Read twice they could disagree, and the reason is only worth anything
    beside the answer it explains.
    """
    assert isinstance(CONFIG, Config)
    assert isinstance(CONFIG_PROBLEM, str)
