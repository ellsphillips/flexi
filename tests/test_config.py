"""Preferences, and every way the file holding them can be wrong.

Config is read once, at import, before Flexi can draw anything to say that it
failed -- `BINDINGS` lists read `CONFIG` at class-definition time. So the only
acceptable outcome of a bad config file is a running application on the default
keys: a typo in a keybinding must never be the reason somebody cannot open
their own time records.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from flexi.config import CONFIG, Config, Defaults, Hotkeys, load_config, section
from flexi.constants import AbsenceType


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
        pytest.param("tick_seconds", 2, id="positive-tick-interval"),
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
        pytest.param("tick_seconds", 0, id="zero-tick-interval"),
        pytest.param("tick_seconds", -1, id="negative-tick-interval"),
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
        "hotkeys:\n  clock_toggle: x\ndefaults:\n  round_to_minutes: 1\n",
    )

    config = load_config(path)

    assert config.hotkeys.clock_toggle == "x", "the section that read was discarded"
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
