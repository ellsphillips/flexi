"""Modals, and the contract every one of them keeps.

``escape`` cancels and dismisses with ``None``. ``enter`` confirms. ``tab`` moves
between fields. A modal that breaks one of those is a bug, and
``tests/tui/test_modal_contract.py`` discovers every :class:`FlexiModal` subclass
by walking the package and asserts it — so a new modal is covered the day it is
written rather than the day somebody remembers to add a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from flexi.constants import AbsenceType, Portion
from flexi.domain.dates import parse_date
from flexi.domain.format import days as fmt_days
from flexi.domain.format import plural


class FlexiModal[ResultT](ModalScreen[ResultT | None]):
    """A dialog with a title, a body, and the two keys every dialog has."""

    HELP_LABEL = "Dialog"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Confirm", show=True, priority=True),
    ]

    title_text: ClassVar[str] = ""
    confirm_label: ClassVar[str] = "Save"

    tall: ClassVar[bool] = False
    """True for a modal whose body can outgrow the screen.

    A dialog is sized to its content, which is right for a question and wrong
    for a form: `.modal` clips at 80% of the screen, so the booking modal's
    last three rows -- the error line and both buttons -- fell off the bottom
    of a 36-row terminal, and a rejected date looked like a key that did
    nothing. A tall modal takes that 80% as a definite height instead, which is
    what lets the body scroll inside it while the title, the error and the
    buttons stay put.
    """

    def compose(self) -> ComposeResult:
        with Container(classes="modal -tall" if self.tall else "modal"):
            yield Static(self.title_text, classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield from self.compose_body()
            yield from self.compose_aside()
            yield Static("", id="modal-error", classes="modal-error")
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="modal-cancel", classes="-quiet")
                yield Button(self.confirm_label, id="modal-confirm", classes="-primary")

    def compose_body(self) -> ComposeResult:
        """The fields between the title and the buttons."""
        return iter(())

    def compose_aside(self) -> ComposeResult:
        """What stays on screen while the fields scroll past it.

        Context rather than input: how much leave is left is what the answer is
        being weighed against, so it is worth as much at the bottom of the form
        as at the top.
        """
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


@dataclass(frozen=True, slots=True)
class AbsenceBooking:
    """What the absence modal collected."""

    when: date
    kind: AbsenceType
    portion: Portion
    note: str | None
    until: date
    """The last day, inclusive. One day booked is one day, not ``None`` -- the
    modal defaults it to ``when``, so nothing downstream has to."""


class AbsenceModal(FlexiModal[AbsenceBooking]):
    """Book one absence: which day, what kind, how much of it, and why."""

    title_text: ClassVar[str] = "Book absence"
    confirm_label: ClassVar[str] = "Book"
    tall: ClassVar[bool] = True

    def __init__(
        self,
        when: date,
        kind: AbsenceType = AbsenceType.ANNUAL,
        *,
        until: date | None = None,
        remaining: float | None = None,
        toil_days: float | None = None,
    ) -> None:
        super().__init__()
        self._when = when
        self._until = until if until and until != when else None
        self._kind = kind
        self._remaining = remaining
        self._toil_days = toil_days

    def compose_body(self) -> ComposeResult:
        yield Label("From" if self._until else "Date", classes="overline")
        yield Input(self._when.isoformat(), id="absence-date", placeholder="YYYY-MM-DD")
        if self._until:
            # A fortnight was selected and the modal showed one date, so `e`
            # wrote fourteen days off the back of a field reading "24 Aug".
            yield Label("Until", classes="overline")
            yield Input(
                self._until.isoformat(), id="absence-until", placeholder="YYYY-MM-DD"
            )

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

    def compose_aside(self) -> ComposeResult:
        yield Static(self._allowance_hint(), classes="caption")

    def _allowance_hint(self) -> str:
        """What is left, so the decision does not need another screen."""
        parts: list[str] = []
        if self._remaining is not None:
            left = self._remaining
            parts.append(f"{fmt_days(left)} {plural(left, 'day')} annual leave left")
        if self._toil_days is not None:
            banked = round(self._toil_days, 1)
            parts.append(f"{fmt_days(banked)} {plural(banked, 'day')} of TOIL banked")
        return " · ".join(parts)

    def result(self) -> AbsenceBooking:
        raw = self.query_one("#absence-date", Input).value.strip()
        try:
            when = parse_date(raw, reference=self._when)
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

        until = when
        if self._until:
            raw_until = self.query_one("#absence-until", Input).value.strip()
            try:
                until = parse_date(raw_until, reference=self._until)
            except ValueError as error:
                raise ValueError(str(error)) from error
            if until < when:
                msg = "The last day is before the first"
                raise ValueError(msg)
        return AbsenceBooking(when, kind, portion, note, until)


class GoToDateModal(FlexiModal[date]):
    """Jump the period anchor to a date, typed however is quickest.

    Everything typed here is read relative to the day already on screen, not to
    today. Somebody browsing last March and typing `12` means the 12th of March,
    and the parameter was called `today` while being handed the anchor.
    """

    title_text: ClassVar[str] = "Go to date"
    confirm_label: ClassVar[str] = "Go"

    def __init__(self, anchor: date) -> None:
        super().__init__()
        self._anchor = anchor

    def compose_body(self) -> ComposeResult:
        yield Input(
            "",
            id="goto-input",
            placeholder="12 · 12 Jun · 2026-06-12 · +3d · -2w",
        )
        yield Static(
            "A bare number is a day of the month on screen. "
            "An offset moves from the day on screen: d, w, m, y.",
            classes="caption",
        )

    def on_mount(self) -> None:
        self.query_one("#goto-input", Input).focus()

    def result(self) -> date:
        return parse_date(
            self.query_one("#goto-input", Input).value, reference=self._anchor
        )


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
