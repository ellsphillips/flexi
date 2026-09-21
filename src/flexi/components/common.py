"""Small widgets every screen needs, so no screen invents its own.

Each is a thin wrapper carrying a class from ``theme/flexi.tcss``, so a screen
picks up a palette change without being edited. :class:`Tone` is the shared
vocabulary, so nothing writes ``"pill--ok"`` as a string.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Unpack

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.dom import DOMNode
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from flexi.components.options import StaticOptions, WidgetOptions

__all__ = (
    "ALL_TONE_CLASSES",
    "GAUGE_TONE_STYLES",
    "MARKER",
    "MIN_GAUGE_WIDTH",
    "NARROW_COLUMNS",
    "TINY_COLUMNS",
    "TONE_CLASSES",
    "TRACK",
    "EmptyIndicator",
    "Gauge",
    "KeyHint",
    "Pill",
    "Rule",
    "StatCard",
    "Tone",
    "mark_width",
    "styled_track",
)

NARROW_COLUMNS: Final = 100
"""Columns the dashboard's two-column layout needs before both are worth reading."""

TINY_COLUMNS: Final = 64
"""Below this, only the records table is drawn; everything else is a jump away."""

TRACK: Final = "━"
MARKER: Final = "┿"
MIN_GAUGE_WIDTH: Final = 10


def styled_track(
    width: int,
    *,
    track: Style,
    fill: Style,
    filled: int = 0,
    mark: tuple[int, Style] | None = None,
) -> Text:
    """A bar: a track, a fill from the left, and one cell picked out.

    Glyphs first, then spans: `Text(plain, spans=...)` drops the *base* style of
    the text it was rebuilt from, so a character swapped in after styling leaves
    the whole track in the default foreground.
    """
    glyphs = [TRACK] * width
    if mark is not None:
        glyphs[mark[0]] = MARKER
    bar = Text("".join(glyphs))
    bar.stylize(track, 0, width)
    if filled:
        bar.stylize(fill, 0, filled)
    if mark is not None:
        bar.stylize(mark[1], mark[0], mark[0] + 1)
    return bar


def mark_width(node: DOMNode, width: int) -> None:
    """Put ``-narrow`` and ``-tiny`` on a node whose terminal is short of columns.

    Terminal CSS has no media query, so the class is the query: a screen calls
    this from ``on_resize`` and its stylesheet says what narrow means for it.
    ``width`` is the terminal's width, never the widget's, so a fold cannot
    change the measurement that caused it.
    """
    node.set_class(width < NARROW_COLUMNS, "-narrow")
    node.set_class(width < TINY_COLUMNS, "-tiny")


class Tone(StrEnum):
    """What a piece of state means, independent of how it is drawn.

    Teal is the accent and is never a state: ``ACCENT`` marks what was being
    looked for, not what is wrong. Failure is ``ERR``, which the stylesheet
    draws in the deficit red the balance uses.
    """

    NEUTRAL = "neutral"
    OK = "ok"
    WARN = "warn"
    ERR = "err"
    ACCENT = "accent"


# A table, not an f-string, so the class names are greppable from the
# stylesheet.
TONE_CLASSES: Final[Mapping[Tone, str]] = MappingProxyType(
    {
        Tone.NEUTRAL: "",
        Tone.OK: "pill--ok",
        Tone.WARN: "pill--warn",
        Tone.ERR: "pill--err",
        Tone.ACCENT: "pill--accent",
    }
)

ALL_TONE_CLASSES: Final[tuple[str, ...]] = tuple(
    name for name in TONE_CLASSES.values() if name
)

GAUGE_TONE_STYLES: Final[Mapping[Tone, str]] = MappingProxyType(
    {
        Tone.NEUTRAL: "gauge--readout-only",
        Tone.OK: "gauge--good",
        Tone.WARN: "gauge--warn",
        Tone.ERR: "gauge--bad",
        Tone.ACCENT: "gauge--readout-only",
    }
)


class Pill(Static):
    """One or two words of state: "on the clock", "3 left", "no data".

    Reports, never acts: a pill is not clickable, and anything that wants to be
    pressed is a ``Button``. Label and tone are reactive, so a validating input
    can update it on every keystroke without touching the DOM.
    """

    DEFAULT_CLASSES: ClassVar[str] = "pill"

    label: reactive[str] = reactive("", init=False)
    tone: reactive[Tone] = reactive(Tone.NEUTRAL, init=False)

    def __init__(
        self,
        label: str = "",
        tone: Tone = Tone.NEUTRAL,
        **kwargs: Unpack[StaticOptions],
    ) -> None:
        super().__init__(label, **kwargs)
        self.set_reactive(Pill.label, label)
        self.set_reactive(Pill.tone, tone)

    def on_mount(self) -> None:
        self._apply_tone()
        self._apply_visibility()

    def set_state(self, label: str, tone: Tone = Tone.NEUTRAL) -> None:
        """Set label and tone together."""
        self.label = label
        self.tone = tone

    def watch_label(self, label: str) -> None:
        self.update(label)
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        """Hide a pill with nothing to say.

        ``.pill`` carries a ground and a ``min-width``, so an empty one draws as
        a six-column block, not as nothing.
        """
        self.display = bool(self.label.strip())

    def watch_tone(self) -> None:
        self._apply_tone()

    def _apply_tone(self) -> None:
        """Apply the tone, clearing the other tone classes first."""
        self.remove_class(*ALL_TONE_CLASSES)
        if applied := TONE_CLASSES[self.tone]:
            self.add_class(applied)


class StatCard(Vertical):
    """One measurement: a label above it, an optional note below.

    Three ``Static``s, not one rendered block: the three lines have different
    colours and weights, which is CSS's job.
    """

    DEFAULT_CLASSES: ClassVar[str] = "stat"

    value: reactive[str] = reactive("", init=False)
    note: reactive[str] = reactive("", init=False)

    def __init__(
        self,
        label: str,
        value: str = "",
        note: str = "",
        **kwargs: Unpack[WidgetOptions],
    ) -> None:
        super().__init__(**kwargs)
        self._label = label
        self.set_reactive(StatCard.value, value)
        self.set_reactive(StatCard.note, note)

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="overline")
        yield Static(self.value, classes="stat-value")
        yield Static(self.note, classes="stat-note")

    def watch_value(self, value: str) -> None:
        # Reactives fire before the first compose, and querying a child that is
        # not mounted yet raises.
        if self.is_mounted:
            self.query_one(".stat-value", Static).update(value)

    def watch_note(self, note: str) -> None:
        if self.is_mounted:
            self.query_one(".stat-note", Static).update(note)


class KeyHint(Horizontal):
    """A key and what it does, in place.

    The footer lists every binding, so this is for the few a screen shows
    where the action is: "space to expand", next to the table it expands.
    """

    def __init__(self, key: str, action: str, **kwargs: Unpack[WidgetOptions]) -> None:
        super().__init__(**kwargs)
        self._key = key
        self._action = action

    def compose(self) -> ComposeResult:
        yield Static(self._key, classes="kbd")
        yield Static(self._action, classes="key-hint-action")


class Rule(Static):
    """A hairline, optionally labelled: how Flexi separates sections.

    Distinct from ``textual.widgets.Rule``, which draws a line and nothing else.
    This one carries the section's name above the line.
    """

    DEFAULT_CLASSES: ClassVar[str] = "rule"

    def __init__(
        self,
        label: str = "",
        *,
        accent: bool = False,
        **kwargs: Unpack[StaticOptions],
    ) -> None:
        super().__init__(label, **kwargs)
        if accent:
            self.add_class("rule--accent")


class EmptyIndicator(Static):
    """A region with nothing in it, drawn as an invitation.

    Hatched, so it reads as an empty region and not as a widget that failed to
    render.
    """

    DEFAULT_CLASSES: ClassVar[str] = "empty-indicator"

    def __init__(
        self, message: str = "Nothing here yet", **kwargs: Unpack[StaticOptions]
    ) -> None:
        super().__init__(message, **kwargs)


class Gauge(Widget):
    """A measurement against a total, with an optional marker where it should be.

    The marker is the pace line: 18.5 days left reads as comfortable or alarming
    depending on how much of the leave year remains. The caller passes the
    :class:`Tone`, since whether that is good news is a leave-policy question.
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

    def __init__(self, label: str, **kwargs: Unpack[WidgetOptions]) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.total = 0.0
        """What the reading is measured against. Zero draws an empty track."""

        self.value: float | None = None
        self.target: float | None = None
        self.readout = ""
        self.tone = Tone.NEUTRAL
        self.compact = False

    def show(
        self,
        value: float | None,
        *,
        readout: str = "",
        total: float,
        target: float | None = None,
        tone: Tone = Tone.NEUTRAL,
        compact: bool = False,
    ) -> None:
        """Draw a reading. ``None`` leaves the track empty, not at zero.

        An unmeasured allowance and one measured at zero are not the same thing.
        ``compact`` drops the bar and keeps the line, so an empty track does not
        cost a row of a sidebar with four other things to say.

        ``total`` is required: a gauge does not remember the last one it was
        given.
        """
        self.value = value
        self.readout = readout
        self.target = target
        self.tone = tone
        self.compact = compact
        self.total = total
        self.styles.height = 1 if compact else 2
        self.refresh()

    def render(self) -> Text:
        width = max(self.content_size.width, MIN_GAUGE_WIDTH)
        if self.compact:
            return self._headline(width)
        return Text("\n").join([self._headline(width), self._bar(width)])

    def _headline(self, width: int) -> Text:
        """Label left, figure right, and the label gives way first.

        `no_wrap` matters: a wrapped headline costs the row the bar is drawn in.
        """
        readout = self.readout or ("—" if self.value is None else f"{self.value:g}")
        label = self.label[: max(0, width - len(readout) - 1)]
        gap = max(width - len(label) - len(readout), 1)
        text = Text(no_wrap=True, end="", overflow="ellipsis")
        text.append(label, self.get_component_rich_style("gauge--label"))
        text.append(" " * gap)
        text.append(readout, self.get_component_rich_style("gauge--readout"))
        return text

    def _bar(self, width: int) -> Text:
        """The track, filled to the reading, with the pace marker on it."""
        marker = self._position(self.target, width)
        reading = self._position(self.value, width)
        return styled_track(
            width,
            track=self.get_component_rich_style("gauge--track"),
            fill=self._fill_style(),
            filled=0 if reading is None else reading + 1,
            mark=(
                None
                if marker is None
                else (marker, self.get_component_rich_style("gauge--target"))
            ),
        )

    def _fill_style(self) -> Style:
        return self.get_component_rich_style(GAUGE_TONE_STYLES[self.tone])

    def _position(self, value: float | None, width: int) -> int | None:
        """Where a value sits on the track, or ``None`` if there is no value."""
        if value is None or self.total <= 0:
            return None
        fraction = value / self.total
        return min(max(round(fraction * (width - 1)), 0), width - 1)
