# The leave screen

`f2`. A continuous, scrolling year of days you can move a cursor over and book
leave on directly.

---

## 1. What it is for, and why it is not the dashboard's calendar

The dashboard already has a calendar. It is a month grid, three lines of code
from a date picker, and its whole job is **navigation** — it moves the period the
records table is showing. It is deliberately small, because it sits in a sidebar
next to four other panels.

This screen has a different job: **management**. Booking a fortnight in August,
noticing that you have three days left and no plan for them, seeing that your
team's usual quiet week is already half-booked. Those are questions about
*months at a time*, and they cannot be answered through a window seven days wide.

So the two coexist, and neither is a worse version of the other:

| | dashboard calendar | leave screen |
|---|---|---|
| job | move the period | book and remove leave |
| span | one month, paged | the whole leave year, scrolled |
| booking | opens a modal | direct, one keystroke |
| ranges | no | yes, `shift` + arrows |

---

## 2. First principles

**A leave year is one continuous thing.** Paging month by month is how a wall
calendar works because paper has edges. A terminal does not: the year scrolls,
months flow into one another, and a fortnight spanning the end of July is drawn
as a fortnight rather than as two halves you have to hold in your head. This is
the "stitched" quality of Apple's calendar, and it is the whole reason the screen
is worth building.

**The cursor is the subject.** Everything acts on where the cursor is — one day,
or the range you extended it into. No modal asks you which date, because you are
standing on it. `A` books annual leave on the selection and it is done.

**Booking must cost one keystroke.** Leave is booked in bursts: you sit down once
and put in the whole summer. A flow that costs a modal, four fields and a confirm
per day is a flow nobody uses twice.

**Refusals are per day, and reported in aggregate.** Booking a fortnight across a
bank holiday should book twelve days and say so, not refuse all fourteen and
leave you to find the bad one. `AbsenceService.book_range` returns what it did
and what it skipped, with reasons.

**The wallet is on screen.** The question behind every booking is "can I afford
this", and an allowance you have to leave the screen to check is an allowance
nobody checks.

---

## 3. Layout

Three responsive states, driven by the terminal's width, as everywhere else.

### Wide, ≥ 100 columns — two columns

```
┌──────────────────────────────────────────────────────────────────────────┐
│ flexi·   Dashboard  Leave  Insights  Settings      Sun 9 Aug · 2026/27   │
├──────────────────────────────────────────────────────────────────────────┤
│ ╭─ Leave ─────────────── 12 booked ─╮ ╭─ Wallet ──────── 20 Oct–19 Oct ─╮│
│ │      M   T   W   T   F   S   S    │ │ ANNUAL   14.5 left of 25        ││
│ │  ── July 2026 ──────────────────  │ │ ━━━━━━━━━━┿━━━━━━━━━━━━━━━━━━━  ││
│ │            1   2   3   4   5      │ │ TOIL          +2:41  (+0.4d)    ││
│ │  6   7   8   9  10  11  12        │ │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   ││
│ │ 13  14  15  16  17  18  19        │ ╰─────────────────────────────────╯│
│ │ 20  21  22  23  24  25  26        │ ╭─ Selected ──────────────────────╮│
│ │ 27  28  29  30  31                │ │ Mon 10 – Fri 14 Aug             ││
│ │  ── August 2026 ────────────────  │ │ 5 working days                  ││
│ │                     1   2         │ │ Annual leave · full days        ││
│ │  3   4   5   6   7   8   9        │ ╰─────────────────────────────────╯│
│ │ 10  11  12  13  14  15  16        │ ╭─ Book ──────────────────────────╮│
│ │ 17  18  19  20  21  22  23        │ │ A annual   S sick   T toil      ││
│ │ 24  25  26  27  28  29  30        │ │ U unpaid   O other  X remove    ││
│ │ 31                                │ │ ␣ half day    e edit    g go to ││
│ │  ── September 2026 ─────────────  │ ╰─────────────────────────────────╯│
│ ╰───────────────────────────────────╯                                    │
├──────────────────────────────────────────────────────────────────────────┤
│ Booked 5 days of annual leave                          ● 14.5 days left  │
│ A annual  S sick  T toil  x remove  ␣ half  v jump  ? help               │
└──────────────────────────────────────────────────────────────────────────┘
```

The right rail is three stacked panels: the wallet, what is selected, and the
key legend. The legend earns its place — this screen has more direct actions
than any other, and the footer strip can only carry seven.

### Narrow, 64–99 columns — one column

The calendar takes the width; the wallet becomes a single line above it
(`ANNUAL 14.5 · SICK 2 · TOIL +2:41`), and the legend goes to the footer and `?`.

### Tiny, < 64 columns — the calendar alone

Days drop from three cells to two. The wallet line truncates to annual leave,
which is the one people are actually rationing.

---

## 4. Interaction

| Key | Does |
|---|---|
| `←` `→` `↑` `↓` / `h` `j` `k` `l` | Move the cursor a day or a week |
| `shift` + any of those | Extend the selection |
| `escape` | Collapse the selection back to one day |
| `A` `S` `T` `U` `O` | Book annual / sick / TOIL / unpaid / other on the selection |
| `space` | Cycle the portion: full → morning → afternoon |
| `x` | Remove whatever is booked on the selection |
| `e` | Open the booking modal on the selection, for a note or an odd case |
| `g` | Go to a date |
| `t` | Today |
| `[` `]` | Previous / next month |
| `pgup` `pgdn` | A screen at a time |

The shifted booking keys are the same five as the dashboard, which is the point:
a key means the same thing everywhere in the application.

`space` cycling the portion *before* booking rather than after is deliberate.
Half days are rare, and a cycle that costs one keystroke on the rare path is
better than a modal on the common one.

---

## 5. Day cells

Each day is three cells wide: two for the number, one for the marker.

| Marker | Means |
|---|---|
| ` ` | nothing booked |
| `●` | a full day, in the day-type colour |
| `◐` | a morning |
| `◑` | an afternoon |
| `◆` | two different half days |
| `·` | a bank holiday |
| dim | not a working day, or not in this leave year |

Colour carries the *type* and the glyph carries the *portion*, so the two
encodings never compete for the same cell — the same rule the year heatmap
follows. A cell is never colour alone: the selected day's type is spelled out in
the "Selected" panel, and `?` lists the scale.

---

## 6. What it is built from

Nothing here is new machinery:

- `Period` already models a leave year and knows its bounds.
- `LedgerService.days()` already loads a span in a fixed number of queries — a
  year is 365 rows and three queries, which is why scrolling one is affordable.
- `AbsenceService` already refuses the illegal bookings and explains itself;
  `book_range` is a loop over it that collects the refusals.
- `WalletService` already computes the allowances.
- The absence modal, the confirm modal, jump mode and the chrome are all shared.

The genuinely new pieces are `YearCalendar` (a scrolling, stitched grid with a
cursor and a selection) and `LeaveScreen` that arranges it.

---

## 7. Jump targets

| Key | Target |
|---|---|
| `c` | The calendar |
| `w` | The wallet |
| `s` | The selection panel |
| `1`–`9` | The first nine months on screen |

Month jumps are this screen's answer to the records table's row jumps: `v` `4`
puts the cursor on the fourth visible month without scrolling.
