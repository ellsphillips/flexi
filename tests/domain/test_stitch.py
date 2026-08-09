from datetime import date, timedelta

import pytest

from flexi.domain.stitch import (
    DAYS_IN_WEEK,
    Selection,
    month_block,
    stitch,
    weekday_initials,
)

# June 2026 starts on a Monday and has 30 days: exactly five weeks, no blanks.
JUNE = (2026, 6)
# August 2026 starts on a Saturday: five leading blanks, and 31 days.
AUGUST = (2026, 8)


# -- laying out a month ----------------------------------------------------


def test_a_month_is_whole_weeks() -> None:
    """Every row is seven cells, so the weekday columns line up."""
    for year, month in (JUNE, AUGUST, (2026, 2), (2024, 2)):
        block = month_block(year, month)
        assert all(len(row) == DAYS_IN_WEEK for row in block.rows)


def test_a_month_starting_on_a_monday_has_no_leading_blanks() -> None:
    """June 2026 begins on a Monday."""
    block = month_block(*JUNE)
    assert block.rows[0][0].date == date(2026, 6, 1)
    assert len(block.rows) == 5


def test_a_month_starting_late_in_the_week_is_padded() -> None:
    """August 2026 begins on a Saturday, so five cells lead it."""
    block = month_block(*AUGUST)
    leading = [cell for cell in block.rows[0] if not cell.filled]
    assert len(leading) == 5
    assert block.rows[0][5].date == date(2026, 8, 1)


def test_no_day_appears_in_two_blocks() -> None:
    """Blanks at a seam, never a borrowed neighbour.

    A grid showing the 30th of June in July's block would make the cursor
    ambiguous and a selection uncountable.
    """
    seen: list[date] = []
    for block in stitch(date(2026, 1, 1), date(2026, 12, 31)):
        seen.extend(cell.date for row in block.rows for cell in row if cell.date)
    assert len(seen) == len(set(seen)) == 365


def test_every_day_of_the_month_is_present() -> None:
    """Nothing is dropped at either end."""
    block = month_block(*AUGUST)
    days = [cell.date.day for row in block.rows for cell in row if cell.date]
    assert days == list(range(1, 32))


def test_a_leap_february_gains_a_day() -> None:
    block = month_block(2024, 2)
    assert sum(1 for row in block.rows for cell in row if cell.filled) == 29


def test_the_first_weekday_rotates_the_grid() -> None:
    """It moves the columns, and the headings with them."""
    monday = month_block(*JUNE, first_weekday=0)
    sunday = month_block(*JUNE, first_weekday=6)
    assert monday.rows[0][0].date == date(2026, 6, 1)
    assert sunday.rows[0][1].date == date(2026, 6, 1)
    assert weekday_initials(6)[0] == "S"


# -- stitching a span ------------------------------------------------------


def test_a_span_covers_whole_months() -> None:
    """A leave year starting on the 20th still wants October drawn whole."""
    blocks = stitch(date(2025, 10, 20), date(2026, 10, 19))
    assert len(blocks) == 13
    assert (blocks[0].year, blocks[0].month) == (2025, 10)
    assert blocks[0].rows[0][2].date == date(2025, 10, 1)
    assert (blocks[-1].year, blocks[-1].month) == (2026, 10)


def test_a_span_inside_one_month_is_one_block() -> None:
    blocks = stitch(date(2026, 6, 8), date(2026, 6, 14))
    assert len(blocks) == 1


def test_a_span_across_a_year_end() -> None:
    blocks = stitch(date(2026, 11, 1), date(2027, 2, 1))
    assert [(b.year, b.month) for b in blocks] == [
        (2026, 11),
        (2026, 12),
        (2027, 1),
        (2027, 2),
    ]


def test_a_block_knows_its_own_bounds() -> None:
    block = month_block(*AUGUST)
    assert block.title == "August 2026"
    assert (block.first, block.last) == (date(2026, 8, 1), date(2026, 8, 31))
    assert block.contains(date(2026, 8, 15))
    assert not block.contains(date(2026, 9, 1))


def test_height_counts_the_title() -> None:
    """The screen scrolls by rows, so a block has to know how tall it is."""
    block = month_block(*JUNE)
    assert block.height == len(block.rows) + 1


# -- the selection ---------------------------------------------------------

MONDAY = date(2026, 8, 10)


def test_a_new_selection_is_one_day() -> None:
    one = Selection.at(MONDAY)
    assert one.single
    assert len(one) == 1
    assert list(one.days()) == [MONDAY]


def test_moving_collapses_it() -> None:
    """An extended selection that is moved becomes a cursor again."""
    extended = Selection.at(MONDAY).extend(4)
    assert len(extended) == 5
    assert extended.move(1).single


def test_extending_keeps_the_anchor() -> None:
    """Which is what lets a selection be pulled back to nothing."""
    selection = Selection.at(MONDAY).extend(4).extend(-4)
    assert selection.single
    assert selection.head == MONDAY


def test_extending_backwards_does_not_invert() -> None:
    """Start and end are ordered however the head got there."""
    selection = Selection.at(MONDAY).extend(-3)
    assert selection.start == MONDAY - timedelta(days=3)
    assert selection.end == MONDAY
    assert len(selection) == 4


def test_collapse_leaves_the_cursor_at_the_head() -> None:
    """Escape puts you where you were driving, not where you started."""
    selection = Selection.at(MONDAY).extend(4)
    assert selection.collapse().head == MONDAY + timedelta(days=4)


def test_membership() -> None:
    selection = Selection.at(MONDAY).extend(4)
    assert MONDAY + timedelta(days=2) in selection
    assert MONDAY + timedelta(days=9) not in selection
    assert "not a date" not in selection


@pytest.mark.parametrize(
    ("extend", "expected"),
    [
        (0, "Mon 10 Aug 2026"),
        (4, "Mon 10 – Fri 14 Aug 2026"),
        (30, "Mon 10 Aug – Wed 9 Sep 2026"),
    ],
)
def test_the_selection_names_itself(extend: int, expected: str) -> None:
    """It drops the repeated month, because a panel is thirty columns wide."""
    assert Selection.at(MONDAY).extend(extend).label() == expected
