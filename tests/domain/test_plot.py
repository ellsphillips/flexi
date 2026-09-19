"""Plotting: dots, bars, stacking and the bounds they are drawn against.

The widget that turns a grid of glyphs into styled text is tested where the
styles are.
"""

from __future__ import annotations

import pytest

from flexi.domain.plot import (
    BRAILLE_BASE,
    DOT_COLUMNS,
    DOT_ROWS,
    Bounds,
    Canvas,
    Glyph,
    Mark,
    Series,
    bar_glyphs,
    braille,
    line_dots,
    plot,
    rule_row,
    stack,
)


def drawn(grid: list[list[Glyph | None]]) -> list[str]:
    """A glyph grid as plain rows, a space where nothing was drawn."""
    return ["".join(" " if cell is None else cell.char for cell in row) for row in grid]


# ---- bounds ----


def test_zero_is_always_in_view() -> None:
    """Hours against a contract: 7:20 to 7:30 must not fill the height."""
    bounds = Bounds.around([Series("a", (7.2, 7.3))], stacked=False)

    assert bounds.low == 0.0
    assert bounds.high == 7.3


def test_deficit_pulls_the_floor_below_zero() -> None:
    bounds = Bounds.around([Series("a", (-3.0, 2.0))], stacked=False)

    assert (bounds.low, bounds.high) == (-3.0, 2.0)


def test_stacked_bars_are_measured_by_their_totals() -> None:
    """Measured series by series, three bands of four would set the ceiling at four."""
    bands = [Series(name, (4.0,), Mark.BAR) for name in ("a", "b", "c")]

    assert Bounds.around(bands, stacked=True).high == 12.0
    assert Bounds.around(bands, stacked=False).high == 4.0


def test_empty_plot_has_a_non_zero_span() -> None:
    """Every position is a division by the span, including on an empty plot."""
    assert Bounds.around([], stacked=False).span == 1.0
    assert Bounds(0.0, 0.0).span == 1.0


def test_bounds_that_run_backwards_are_refused() -> None:
    with pytest.raises(ValueError, match="run backwards"):
        Bounds(10.0, 0.0)


# ---- the canvas ----


def test_cell_carries_eight_independent_dots() -> None:
    canvas = Canvas(1, 1)
    for x in range(DOT_COLUMNS):
        for y in range(DOT_ROWS):
            canvas.light(x, y)

    assert canvas.rows() == ["⣿"]


def test_canvas_needs_positive_extents() -> None:
    with pytest.raises(ValueError, match="positive extents"):
        Canvas(0, 4)


@pytest.mark.parametrize("dot", [(-1, 0), (0, -1), (99, 0), (0, 99)])
def test_dot_outside_the_canvas_is_dropped(dot: tuple[int, int]) -> None:
    """The rasteriser walks whole pixels between samples, so it clips at the dot."""
    canvas = Canvas(2, 2)
    canvas.light(*dot)

    assert canvas.rows() == [braille(0) * 2] * 2


# ---- lines ----


def test_line_is_joined_and_not_dotted() -> None:
    """The samples are the corners; the rasteriser fills what is between them."""
    canvas = Canvas(8, 2)
    line_dots((0.0, 1.0), Bounds(0.0, 1.0), canvas)

    assert all(row.strip(braille(0)) for row in canvas.rows()), "both rows are drawn"
    lit = sum(cell != braille(0) for row in canvas.rows() for cell in row)
    assert lit >= canvas.width, "every column carries part of the stroke"


def test_one_reading_is_a_point_and_none_is_nothing() -> None:
    """A leave year one day old has one week in it, and still draws."""
    single = Canvas(4, 1)
    line_dots((5.0,), Bounds(0.0, 10.0), single)
    assert any(cell != braille(0) for cell in single.rows()[0])

    empty = Canvas(4, 1)
    line_dots((), Bounds(0.0, 10.0), empty)
    assert empty.rows() == [braille(0) * 4]


# ---- bars ----


def test_sample_owns_a_slice_of_the_width() -> None:
    """A fortnight in forty columns is two cells a bar, not a hairline each."""
    grid = bar_glyphs(
        [Series("a", (1.0, 1.0), Mark.BAR)], Bounds(0.0, 1.0), 8, 1, stacked=False
    )

    assert drawn(grid) == ["████████"]


def test_bars_are_drawn_in_eighths_of_a_cell() -> None:
    grid = bar_glyphs(
        [Series("a", (0.5,), Mark.BAR)], Bounds(0.0, 1.0), 1, 1, stacked=False
    )

    assert drawn(grid) == ["▄"]


def test_bar_of_nothing_draws_nothing() -> None:
    grid = bar_glyphs(
        [Series("a", (0.0,), Mark.BAR)], Bounds(0.0, 1.0), 2, 1, stacked=False
    )

    assert drawn(grid) == ["  "]


def test_bars_with_no_samples_leave_an_empty_grid() -> None:
    """A period that has not started has series and no values in them."""
    assert drawn(bar_glyphs([Series("a", ())], Bounds(0, 1), 2, 1, stacked=False)) == [
        "  "
    ]
    assert drawn(bar_glyphs([], Bounds(0, 1), 2, 1, stacked=False)) == ["  "]


# ---- stacking ----


def test_stacking_puts_each_band_on_the_ones_below_it() -> None:
    assert stack([Series("a", (1.0, 2.0)), Series("b", (3.0, 4.0))]) == [
        (1.0, 4.0),
        (2.0, 6.0),
    ]


def test_stacked_bar_is_as_tall_as_its_total() -> None:
    bands = [Series("a", (1.0,), Mark.BAR), Series("b", (1.0,), Mark.BAR)]

    grid = bar_glyphs(bands, Bounds(0.0, 2.0), 1, 2, stacked=True)

    assert drawn(grid) == ["█", "█"]


def test_stacked_bands_keep_their_own_tones() -> None:
    bands = [
        Series("a", (1.0,), Mark.BAR, "annual"),
        Series("b", (1.0,), Mark.BAR, "sick"),
    ]

    grid = bar_glyphs(bands, Bounds(0.0, 2.0), 1, 2, stacked=True)

    assert [row[0].tone for row in grid if row[0]] == ["sick", "annual"]


# ---- both at once ----


def test_line_is_drawn_over_the_bars() -> None:
    """Bars are the context, and one drawn over the line hides it where they meet."""
    grid = plot(
        [
            Series("bars", (1.0, 1.0), Mark.BAR, "series"),
            Series("line", (1.0, 1.0), Mark.LINE, "target"),
        ],
        4,
        2,
    )

    assert grid[0][0] is not None
    assert grid[0][0].tone == "target", "the line took the cell"


def test_series_must_be_named() -> None:
    with pytest.raises(ValueError, match="needs a name"):
        Series("", (1.0,))


# ---- the reference rule ----


def test_rule_gives_way_to_a_bar_it_crosses() -> None:
    """Bars are drawn before the rule, so the rule never cuts a gap through one."""
    grid = plot([Series("tall", (2.0,), Mark.BAR)], 4, 2, rule=1.0)

    tones = [cell.tone for row in grid for cell in row if cell]
    assert set(tones) == {"series"}, "the bar kept every cell the rule wanted"


def test_rule_shows_beside_a_bar_short_of_it() -> None:
    """The half of the row the bar left empty is still the threshold."""
    grid = plot([Series("short", (2.0, 0.0), Mark.BAR)], 4, 2, rule=1.0)

    tones = {cell.tone for row in grid for cell in row if cell}
    assert tones == {"series", "rule"}


def test_rule_reaches_across_an_empty_plot() -> None:
    grid = plot([], 4, 3, rule=0.0)

    assert [cell.char if cell else " " for cell in grid[-1]] == ["╌"] * 4


def test_rule_pulls_itself_into_view() -> None:
    """The bounds widen to hold a threshold that falls outside them."""
    grid = plot([Series("high", (10.0, 10.0), Mark.LINE)], 4, 4, rule=20.0)

    assert any(cell and cell.tone == "rule" for row in grid for cell in row)


def test_rule_outside_its_bounds_is_not_drawn() -> None:
    """`rule_row` answers for a plot whose bounds were fixed elsewhere."""
    assert rule_row(99.0, Bounds(0.0, 5.0), 10) is None
    assert rule_row(0.0, Bounds(0.0, 5.0), 10) == 9


# ---- two lines ----


def lit(grid: list[list[Glyph | None]]) -> set[tuple[int, int, int]]:
    """Every braille dot lit in a grid, as ``(row, column, bit)``."""
    return {
        (row, column, bit)
        for row, cells in enumerate(grid)
        for column, cell in enumerate(cells)
        if cell is not None
        for bit in (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80)
        if (ord(cell.char) - BRAILLE_BASE) & bit
    }


RISING = Series("rising", (0.0, 1.0, 2.0, 3.0), Mark.LINE, "series")
FALLING = Series("falling", (3.0, 2.0, 1.0, 0.0), Mark.LINE, "compare")


def test_two_lines_keep_every_dot_the_other_lights() -> None:
    """A cell at a time, the second glyph would blank the first at a crossing."""
    merged = lit(plot([RISING, FALLING], 6, 3))

    assert lit(plot([RISING], 6, 3)) <= merged
    assert lit(plot([FALLING], 6, 3)) <= merged
    assert lit(plot([FALLING, RISING], 6, 3)) == merged


def test_shared_cell_wears_the_later_tone() -> None:
    grid = plot([RISING, FALLING], 6, 3)
    shared = grid[1][2]

    assert shared is not None
    assert shared.tone == "compare"
    assert lit([[shared]]) == {(0, 0, bit) for bit in (0x01, 0x10, 0x20, 0x40)}
