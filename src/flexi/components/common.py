"""Small widgets every screen needs, so no screen invents its own.

Six things, deliberately: a pill for state, a stat for a measurement, a gauge for
a measurement with a target, a key hint for a shortcut, a rule for a section
break, and an empty indicator for a region with nothing in it. Each is a thin
wrapper over a Textual widget whose entire job is to carry a class from
``theme/flexi.tcss`` — the styling lives there, not here, so a screen written by
somebody else picks up a palette change without being edited.

:class:`Tone` is the vocabulary they share. It exists so that "this went well" is
expressed the same way in a pill, a gauge and a status bar, and so that a screen
never writes ``"pill--ok"`` as a string and gets it wrong.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TYPE_CHECKING, ClassVar, Final

from rich.text import Text
from textual.app import ComposeResult, RenderResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

if TYPE_CHECKING:
    from rich.style import Style
    from textual.dom import DOMNode

NARROW_COLUMNS: Final = 100
"""Columns the dashboard's two-column layout needs before both are worth reading."""

TINY_COLUMNS: Final = 64
"""Below this, only the records table is drawn; everything else is a jump away."""

TRACK: Final = "━"
MARKER: Final = "┿"
MIN_GAUGE_WIDTH: Final = 10


def mark_width(node: DOMNode, width: int) -> None:
    """Put ``-narrow`` and ``-tiny`` on a node whose terminal is short of columns.

    Terminal CSS has no media query, so the class is the query: a screen calls
    this from ``on_resize`` and its stylesheet says what narrow means for it.
    Driven by the terminal's width in every case, never by the widget's own,
    which is what keeps a fold from changing the measurement that caused it.
    """
    node.set_class(width < NARROW_COLUMNS, "-narrow")
    node.set_class(width < TINY_COLUMNS, "-tiny")


class Tone(StrEnum):
    """What a piece of state means, independent of how it is drawn.

    Teal is the accent and is never a state: ``ACCENT`` is for "this is the thing
    you came here for", not "this is wrong". Failure is ``ERR``, which the
    stylesheet draws in the deficit red the balance already uses.
    """

    NEUTRAL = "neutral"
    OK = "ok"
    WARN = "warn"
    ERR = "err"
    ACCENT = "accent"


# Kept as a table rather than an f-string so the class names are greppable from
# the stylesheet, which is where somebody debugging a colour will start.
TONE_CLASSES: Final[dict[Tone, str]] = {
    Tone.NEUTRAL: "",
    Tone.OK: "pill--ok",
    Tone.WARN: "pill--warn",
    Tone.ERR: "pill--err",
    Tone.ACCENT: "pill--accent",
}

_ALL_TONE_CLASSES: Final[tuple[str, ...]] = tuple(
    name for name in TONE_CLASSES.values() if name
)

GAUGE_TONE_STYLES: Final[dict[Tone, str]] = {
    Tone.NEUTRAL: "gauge--readout-only",
    Tone.OK: "gauge--good",
    Tone.WARN: "gauge--warn",
    Tone.ERR: "gauge--bad",
    Tone.ACCENT: "gauge--readout-only",
}


class Pill(Static):
    """One or two words of state: "on the clock", "3 left", "no data".

    Reports, never acts — a pill is not clickable, and anything that wants to be
    pressed should be a ``Button``. Label and tone are reactive so a validating
    input can update it on every keystroke without touching the DOM.
    """

    DEFAULT_CLASSES: ClassVar[str] = "pill"

    label: reactive[str] = reactive("", init=False)
    tone: reactive[Tone] = reactive(Tone.NEUTRAL, init=False)

    def __init__(self, label: str = "", tone: Tone = Tone.NEUTRAL, **kwargs: Any) -> None:
        super().__init__(label, **kwargs)
        self.set_reactive(Pill.label, label)
        self.set_reactive(Pill.tone, tone)

    def on_mount(self) -> None:
        self._apply_tone()
        self._apply_visibility()

    def set_state(self, label: str, tone: Tone = Tone.NEUTRAL) -> None:
        """Set both at once — the pair is what a caller actually has."""
        self.label = label
        self.tone = tone

    def watch_label(self, label: str) -> None:
        self.update(label)
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        """A pill with nothing to say is not drawn.

        ``.pill`` carries a ground and a ``min-width``, so an empty one is not
        invisible — it is a six-column block with no text in it. Reporting
        nothing has to render as nothing.
        """
        self.display = bool(self.label.strip())

    def watch_tone(self) -> None:
        self._apply_tone()

    def _apply_tone(self) -> None:
        """Tone classes are mutually exclusive, so removing all of them first is
        cheaper to reason about than tracking which one is on."""
        self.remove_class(*_ALL_TONE_CLASSES)
        if applied := TONE_CLASSES[self.tone]:
            self.add_class(applied)


class StatCard(Vertical):
    """One measurement: a label above it, an optional note below.

    Three ``Static``s rather than one rendered block, because the three lines
    have different colours and different weights, and CSS is where that belongs.
    """

    DEFAULT_CLASSES: ClassVar[str] = "stat"

    value: reactive[str] = reactive("", init=False)
    note: reactive[str] = reactive("", init=False)

    def __init__(self, label: str, value: str = "", note: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._label = label
        self.set_reactive(StatCard.value, value)
        self.set_reactive(StatCard.note, note)

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="overline")
        yield Static(self.value, classes="stat-value")
        yield Static(self.note, classes="stat-note")

    def watch_value(self, value: str) -> None:
        # Reactives fire before the first compose, and querying a child that has
        # not been mounted yet raises rather than returning nothing.
        if self.is_mounted:
            self.query_one(".stat-value", Static).update(value)

    def watch_note(self, note: str) -> None:
        if self.is_mounted:
            self.query_one(".stat-note", Static).update(note)


class KeyHint(Horizontal):
    """A key and what it does, in place.

    The footer already lists every binding, so this is for the few a screen wants
    to teach where the action is — "space to expand", next to the table it
    expands — rather than a second copy of the footer.
    """

    def __init__(self, key: str, action: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._key = key
        self._action = action

    def compose(self) -> ComposeResult:
        yield Static(self._key, classes="kbd")
        yield Static(self._action, classes="key-hint-action")


class Rule(Static):
    """A hairline, optionally labelled — how Flexi separates sections.

    Distinct from ``textual.widgets.Rule``, which draws a line and nothing else.
    This one carries the section's name above the line, which is the editorial
    form of a heading and the reason there are no boxes inside a module.
    """

    DEFAULT_CLASSES: ClassVar[str] = "rule"

    def __init__(self, label: str = "", *, accent: bool = False, **kwargs: Any) -> None:
        super().__init__(label, **kwargs)
        if accent:
            self.add_class("rule--accent")


class EmptyIndicator(Static):
    """A region with nothing in it, drawn as an invitation rather than a blank.

    Hatched, so it reads as an empty region rather than as a widget that failed
    to render — the failure an unstyled gap is most often mistaken for.
    """

    DEFAULT_CLASSES: ClassVar[str] = "empty-indicator"

    def __init__(self, message: str = "Nothing here yet", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class Gauge(Widget):
    """A measurement against a total, with an optional marker where it should be.

    A number of days remaining is unreadable on its own: 18.5 is either
    comfortable or alarming depending entirely on how much of the leave year is
    left. So the gauge draws a marker where the pace line sits and colours the
    fill by the caller's verdict — the sentence "you have spent six of
    twenty-five and you are four months in" written in two rows.

    The widget makes no judgement of its own. The caller passes a :class:`Tone`,
    because whether an underspent allowance is good news is a question about
    leave policy, not about bars.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "gauge--label",
        "gauge--readout",
        "gauge--track",
        "gauge--target",
        "gauge--good",
        "gauge--warn",
        "gauge--bad",
        "gauge--readout-only",
    }

    def __init__(self, label: str, *, total: float = 1.0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.total = total
        self.value: float | None = None
        self.target: float | None = None
        self.readout = ""
        self.tone = Tone.NEUTRAL

    def show(
        self,
        value: float | None,
        *,
        readout: str = "",
        total: float | None = None,
        target: float | None = None,
        tone: Tone = Tone.NEUTRAL,
    ) -> None:
        """Draw a reading. ``None`` leaves the track empty rather than at zero.

        An unmeasured allowance and an allowance measured at zero are not the
        same thing, and a gauge that drew them identically would say a fresh
        install had spent nothing when it does not yet know.
        """
        self.value = value
        self.readout = readout
        self.target = target
        self.tone = tone
        if total is not None:
            self.total = total
        self.refresh()

    def render(self) -> RenderResult:
        width = max(self.content_size.width, MIN_GAUGE_WIDTH)
        return Text("\n").join([self._headline(width), self._bar(width)])

    def _headline(self, width: int) -> Text:
        readout = self.readout or ("—" if self.value is None else f"{self.value:g}")
        gap = max(width - len(self.label) - len(readout), 1)
        return Text.assemble(
            (self.label, self.get_component_rich_style("gauge--label")),
            " " * gap,
            (readout, self.get_component_rich_style("gauge--readout")),
        )

    def _bar(self, width: int) -> Text:
        bar = Text(TRACK * width, style=self.get_component_rich_style("gauge--track"))
        if (filled := self._position(self.value, width)) is not None:
            bar.stylize(self._fill_style(), 0, filled + 1)
        if (marker := self._position(self.target, width)) is not None:
            bar.stylize(self.get_component_rich_style("gauge--target"), marker, marker + 1)
            plain = list(bar.plain)
            plain[marker] = MARKER
            return Text("".join(plain), spans=bar.spans)
        return bar

    def _fill_style(self) -> Style:
        return self.get_component_rich_style(GAUGE_TONE_STYLES[self.tone])

    def _position(self, value: float | None, width: int) -> int | None:
        """Where a value sits on the track, or ``None`` if there is no value."""
        if value is None or self.total <= 0:
            return None
        fraction = value / self.total
        return min(max(round(fraction * (width - 1)), 0), width - 1)
