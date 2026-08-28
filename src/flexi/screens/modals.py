"""Modals, and the contract every one of them keeps.

``escape`` cancels and dismisses with ``None``. ``enter`` confirms. ``tab`` moves
between fields. A modal that breaks one of those is a bug, and
``tests/tui/test_modal_contract.py`` discovers every :class:`FlexiModal` subclass
by walking the package and asserts it — so a new modal is covered the day it is
written rather than the day somebody remembers to add a test.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, VerticalScroll
from textual.dom import DOMNode
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from flexi.constants import AbsenceType, Portion
from flexi.domain.dates import parse_date
from flexi.domain.format import clock, hm, plural, short_date
from flexi.domain.format import days as fmt_days
from flexi.domain.ledger import Segment
from flexi.services.settings import parse_clock_time

__all__ = (
    "AbsenceBooking",
    "AbsenceModal",
    "ConfirmModal",
    "Correction",
    "CorrectionModal",
    "CorrectionsModal",
    "FlexiModal",
    "GoToDateModal",
    "correction_line",
    "selected_name",
)


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

    @property
    def modal_title(self) -> str:
        """The title rendered for this modal instance."""
        return self.title_text

    def compose(self) -> ComposeResult:
        with Container(classes="modal -tall" if self.tall else "modal"):
            yield Static(self.modal_title, classes="modal-title")
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

    @property
    def modal_title(self) -> str:
        """The title supplied with this particular confirmation."""
        return self._title

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
            selected_name(self, "#absence-type", fallback=AbsenceType.ANNUAL.value)
        )
        portion = Portion(
            selected_name(self, "#absence-portion", fallback=Portion.FULL.value)
        )
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


@dataclass(frozen=True, slots=True)
class Correction:
    """A stretch of work somebody is recording after the fact."""

    day: date
    opened: time
    closed: time


class CorrectionModal(FlexiModal[Correction]):
    """Record work on a day nobody clocked at the time.

    Three fields and no clever ones. The day defaults to whatever was selected,
    because the commonest correction is for the day being looked at, and the
    times are read with the same grammar the rest of Flexi reads a clock time
    with -- `9`, `9:15`, `0915` and `9am` are all a quarter past nine.
    """

    title_text: ClassVar[str] = "Record work"
    confirm_label: ClassVar[str] = "Record"

    def __init__(self, day: date) -> None:
        super().__init__()
        self._day = day

    @property
    def modal_title(self) -> str:
        return f"Record work on {short_date(self._day)}"

    def compose_body(self) -> ComposeResult:
        yield Label("From", classes="overline")
        yield Input("", id="correction-from", placeholder="9:00")
        yield Label("To", classes="overline")
        yield Input("", id="correction-to", placeholder="17:00")
        yield Static(
            "For a day you worked and did not clock. It counts for everything a "
            "punched session counts for, and is drawn apart from one.",
            classes="caption",
        )

    def on_mount(self) -> None:
        self.query_one("#correction-from", Input).focus()

    def result(self) -> Correction:
        return Correction(
            day=self._day,
            opened=self._time("#correction-from", "a start"),
            closed=self._time("#correction-to", "an end"),
        )

    def _time(self, selector: str, what: str) -> time:
        """One field, read as a clock time, or a sentence saying why not."""
        typed = self.query_one(selector, Input).value.strip()
        if not typed:
            msg = f"Give {what} time"
            raise ValueError(msg)
        return time(*parse_clock_time(typed))


class CorrectionsModal(FlexiModal[None]):
    """Every correction in the period, so they can be read back as a set.

    A review rather than a form: what was typed from memory is the part of the
    record worth checking, and on a punch strip a correction is one fill among
    several. Listed together they are a short answer to "what did I claim?".
    """

    HELP_LABEL = "Corrections"

    title_text: ClassVar[str] = "Corrections"
    confirm_label: ClassVar[str] = "Close"
    tall: ClassVar[bool] = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("enter", "cancel", "Close", show=True, priority=True),
    ]

    def __init__(self, period: str, corrections: Sequence[Segment]) -> None:
        super().__init__()
        self._period = period
        self._corrections = tuple(corrections)

    @property
    def modal_title(self) -> str:
        return f"Corrections · {self._period}"

    def compose_body(self) -> ComposeResult:
        if not self._corrections:
            yield Static(
                "Nothing recorded after the fact in this period.",
                classes="caption",
            )
            return
        for segment in self._corrections:
            yield Static(correction_line(segment), classes="correction-row")

    def compose_aside(self) -> ComposeResult:
        yield Static(self._summary(), classes="caption")

    def _summary(self) -> str:
        if not self._corrections:
            return ""
        total = sum(
            (
                segment.end - segment.start
                for segment in self._corrections
                if segment.end
            ),
            timedelta(),
        )
        counted = len(self._corrections)
        return f"{counted} {plural(counted, 'correction')} · {hm(total)} recorded"


def correction_line(segment: Segment) -> str:
    """One correction, as a date and the window it claims."""
    finish = segment.end
    window = "open" if finish is None else f"{clock(segment.start)}–{clock(finish)}"
    length = "" if finish is None else f"  {hm(finish - segment.start)}"
    return f"{short_date(segment.start.date()):<12} {window}{length}"


def selected_name(screen: DOMNode, selector: str, *, fallback: str) -> str:
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
