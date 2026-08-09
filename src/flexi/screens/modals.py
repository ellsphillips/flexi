"""Modals, and the contract every one of them keeps.

``escape`` cancels and dismisses with ``None``. ``enter`` confirms. ``tab`` moves
between fields. A modal that breaks one of those is a bug, and
``tests/tui/test_modal_contract.py`` discovers every :class:`FlexiModal` subclass
by walking the package and asserts it — so a new modal is covered the day it is
written rather than the day somebody remembers to add a test.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from flexi.constants import AbsenceType, Portion
from flexi.domain.format import days as fmt_days


class FlexiModal[ResultT](ModalScreen[ResultT | None]):
    """A dialog with a title, a body, and the two keys every dialog has."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Confirm", show=True, priority=True),
    ]

    title_text: ClassVar[str] = ""
    confirm_label: ClassVar[str] = "Save"

    def compose(self) -> ComposeResult:
        with Container(classes="modal"):
            yield Static(self.title_text, classes="modal-title")
            yield from self.compose_body()
            yield Static("", id="modal-error", classes="modal-error")
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="modal-cancel", classes="-quiet")
                yield Button(self.confirm_label, id="modal-confirm", classes="-primary")

    def compose_body(self) -> ComposeResult:
        """The fields between the title and the buttons."""
        return iter(())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_confirm(self) -> None:
        """Validate and dismiss. Subclasses override :meth:`result`."""
        try:
            value = self.result()
        except ValueError as error:
            self.show_error(str(error))
            return
        self.dismiss(value)

    def result(self) -> ResultT:
        """The value this modal was opened to collect.

        Raise :class:`ValueError` with a sentence the user can act on; it is
        shown under the fields rather than replacing them, so what they typed
        stays on screen next to what was wrong with it.
        """
        raise NotImplementedError

    def show_error(self, message: str) -> None:
        self.query_one("#modal-error", Static).update(message)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "modal-confirm":
            self.action_confirm()
        else:
            self.action_cancel()


class ConfirmModal(FlexiModal[bool]):
    """A yes-or-no question. Dismisses ``True``, ``False`` or ``None``."""

    confirm_label: ClassVar[str] = "Yes"
    title_text: ClassVar[str] = "Are you sure?"

    def __init__(self, question: str, *, title: str = "Are you sure?") -> None:
        super().__init__()
        self._title = title
        self._question = question

    def compose(self) -> ComposeResult:
        # The title varies per question, and `title_text` is a class variable
        # every other modal sets once. Overriding compose is cheaper than making
        # the attribute an instance one on the base for the sake of this modal.
        self.title_text = self._title  # type: ignore[misc]
        yield from super().compose()

    def compose_body(self) -> ComposeResult:
        yield Static(self._question)

    def result(self) -> bool:
        return True

    def action_cancel(self) -> None:
        self.dismiss(False)


class AbsenceBooking:
    """What the absence modal collected."""

    __slots__ = ("kind", "note", "portion", "when")

    def __init__(
        self, when: date, kind: AbsenceType, portion: Portion, note: str | None
    ) -> None:
        self.when = when
        self.kind = kind
        self.portion = portion
        self.note = note


class AbsenceModal(FlexiModal[AbsenceBooking]):
    """Book one absence: which day, what kind, how much of it, and why."""

    title_text: ClassVar[str] = "Book absence"
    confirm_label: ClassVar[str] = "Book"

    def __init__(
        self,
        when: date,
        kind: AbsenceType = AbsenceType.ANNUAL,
        *,
        remaining: float | None = None,
        toil_days: float | None = None,
    ) -> None:
        super().__init__()
        self._when = when
        self._kind = kind
        self._remaining = remaining
        self._toil_days = toil_days

    def compose_body(self) -> ComposeResult:
        yield Label("Date", classes="overline")
        yield Input(self._when.isoformat(), id="absence-date", placeholder="YYYY-MM-DD")

        yield Label("Type", classes="overline")
        with RadioSet(id="absence-type"):
            for kind in AbsenceType:
                yield RadioButton(kind.label, value=kind is self._kind, name=kind.value)

        yield Label("How much", classes="overline")
        with RadioSet(id="absence-portion"):
            for portion in Portion:
                yield RadioButton(
                    portion.label, value=portion is Portion.FULL, name=portion.value
                )

        yield Label("Note", classes="overline")
        yield Input("", id="absence-note", placeholder="Required for Other")
        yield Static(self._allowance_hint(), classes="caption")

    def _allowance_hint(self) -> str:
        """What is left, so the decision does not need another screen."""
        parts: list[str] = []
        if self._remaining is not None:
            parts.append(f"{fmt_days(self._remaining)} days annual leave left")
        if self._toil_days is not None:
            parts.append(f"{fmt_days(round(self._toil_days, 1))} days of TOIL banked")
        return " · ".join(parts)

    def result(self) -> AbsenceBooking:
        raw = self.query_one("#absence-date", Input).value.strip()
        try:
            when = parse_date(raw, today=self._when)
        except ValueError as error:
            raise ValueError(str(error)) from error

        kind = AbsenceType(
            _selected_name(self, "#absence-type", AbsenceType.ANNUAL.value)
        )
        portion = Portion(_selected_name(self, "#absence-portion", Portion.FULL.value))
        note = self.query_one("#absence-note", Input).value.strip() or None

        if kind.requires_note and not note:
            msg = "Other absence needs a note saying what it is"
            raise ValueError(msg)
        return AbsenceBooking(when, kind, portion, note)


class GoToDateModal(FlexiModal[date]):
    """Jump the period anchor to a date, typed however is quickest."""

    title_text: ClassVar[str] = "Go to date"
    confirm_label: ClassVar[str] = "Go"

    def __init__(self, today: date) -> None:
        super().__init__()
        self._today = today

    def compose_body(self) -> ComposeResult:
        yield Input(
            "",
            id="goto-input",
            placeholder="12 · 12 Jun · 2026-06-12 · +3d · -2w",
        )
        yield Static(
            "A bare number is a day of the current month. "
            "An offset moves from today: d, w, m, y.",
            classes="caption",
        )

    def on_mount(self) -> None:
        self.query_one("#goto-input", Input).focus()

    def result(self) -> date:
        return parse_date(self.query_one("#goto-input", Input).value, today=self._today)


OFFSET_UNITS = {"d": 1, "w": 7}


def parse_date(raw: str, *, today: date) -> date:
    """Read the several ways somebody might type a date.

    Accepts ``2026-06-12``, ``12 Jun``, ``12`` (this month), and offsets from
    today like ``+3d`` or ``-2w``. Anything else raises with a sentence naming
    the forms it does understand, because "invalid date" tells nobody anything.
    """
    text = raw.strip()
    if not text:
        msg = "Type a date, a day of the month, or an offset like +3d"
        raise ValueError(msg)

    if text[0] in "+-" and text[-1].lower() in "dwmy":
        return _apply_offset(text, today)

    for pattern in ("%Y-%m-%d", "%d %b %Y", "%d %b", "%d/%m/%Y", "%d/%m"):
        try:
            parsed = date.fromisoformat(text) if pattern == "%Y-%m-%d" else None
            if parsed is None:
                from datetime import datetime

                parsed = datetime.strptime(text, pattern).date()  # noqa: DTZ007
                if "%Y" not in pattern:
                    parsed = parsed.replace(year=today.year)
        except ValueError:
            continue
        else:
            return parsed

    if text.isdigit():
        day = int(text)
        try:
            return today.replace(day=day)
        except ValueError as error:
            msg = f"{today.strftime('%B')} has no day {day}"
            raise ValueError(msg) from error

    msg = "Try 2026-06-12, 12 Jun, 12, or an offset like +3d"
    raise ValueError(msg)


def _apply_offset(text: str, today: date) -> date:
    unit = text[-1].lower()
    try:
        count = int(text[:-1])
    except ValueError as error:
        msg = "An offset looks like +3d, -2w, +1m"
        raise ValueError(msg) from error
    if unit in OFFSET_UNITS:
        return today + timedelta(days=count * OFFSET_UNITS[unit])
    months = count * (12 if unit == "y" else 1)
    total = today.year * 12 + today.month - 1 + months
    year, month = total // 12, total % 12 + 1
    import calendar

    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def _selected_name(screen: ModalScreen[Any], selector: str, fallback: str) -> str:
    """The ``name`` of the pressed radio button, or a fallback.

    Radio sets report the pressed *button*, and Flexi puts the enum value in its
    ``name`` so the modal never has to map a label back to a member — a mapping
    that silently breaks the moment a label is reworded.
    """
    radio_set = screen.query_one(selector, RadioSet)
    pressed = radio_set.pressed_button
    if pressed is None or pressed.name is None:
        return fallback
    return pressed.name
