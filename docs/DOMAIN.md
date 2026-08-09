# Domain model

Everything on screen is a view of four things: **events**, **sessions**,
**absences**, and the **settings** that say what a normal day looks like. This
document is the contract for all four, plus the arithmetic that turns them into
the one number the application exists to show.

`flexi/domain/` is pure Python — no Textual, no SQLAlchemy. If a calculation can
be expressed over dates, durations and a settings record, it belongs there and it
is tested without a database.

---

## 1. The one number

**Flexi balance = worked − expected**, accumulated over the leave year, minus any
TOIL already taken as time off.

```
balance(as_of) =  Σ worked_hours(d)            for d in leave_year_start..as_of
               −  Σ expected_hours(d)          for d in leave_year_start..as_of
               −  Σ toil_taken_hours(d)        for d in leave_year_start..as_of
               +  Σ adjustments(d)             for d in leave_year_start..as_of
```

**Adjustments are the only stored term.** Everything else is derived from clock
events, which is right until somebody installs Flexi in August with a leave year
that started the previous October — two hundred untracked working days each
expect their contracted hours, and the balance opens at minus ninety. Deleting
the records would lose the proof of what did happen and would not survive the
next recomputation, so `flexi balance zero` writes one signed row instead.

It settles to *yesterday* by default. Today is not over, and absorbing its
contracted hours before they have been worked would leave the evening looking
like unearned overtime.

`expected_hours(d)` is the crux:

| Day | expected |
|---|---|
| Not a working day (per `working_days`) | `0` |
| Bank holiday in the configured division | `0` |
| Whole-day absence of any type | `0` |
| Half-day absence | `contracted_hours / 2` |
| Ordinary working day | `contracted_hours` |

So a day you booked as annual leave neither earns nor costs flexi. A day you
worked six hours against a 7.4-hour contract costs you 1.4 hours of balance. A
Saturday you worked earns you the lot.

`toil_taken_hours(d)` is a whole (or half) day of `FLEXI` absence valued at
`contracted_hours` (or half). Taking a TOIL day is the *withdrawal* side of the
same account the surplus accrues into, which is why TOIL is not counted as a
separate allowance the way annual leave is — it has no entitlement, only a
balance.

**Open sessions count.** If you are on the clock right now, `worked_hours(today)`
includes the time since you clocked in, so the balance ticks up while you watch
it. That is deliberate, and it is why the dashboard refreshes on a timer.

**Precision.** All arithmetic is in whole seconds, held as `datetime.timedelta`.
Hours only appear at the formatting boundary. Never store or compare a float of
hours; `7.4` is not representable and a week of rounding it produces a balance
that disagrees with the sum of its own rows.

---

## 2. Tables

### `settings` (single row)

| Column | Type | Meaning |
|---|---|---|
| `leave_year_start` | `str` "MM-DD" | Anniversary the allowances reset on. |
| `working_days` | `str` "0,1,2,3,4" | Weekday indices, Monday = 0. |
| `bank_holiday_division` | `str` | GOV.UK division: `england-and-wales`, `scotland`, `northern-ireland`. |
| `auto_close_time` | `str` "HH:MM" | A session still open at this time on a later day is closed here rather than left running. |
| `contracted_minutes` | `int` | Minutes in a standard working day. Default `444` (7h 24m). **New in v2** — replaces the `STANDARD_DAY_HOURS = 7.4` constant. |
| `day_window_start` | `str` "HH:MM" | Left edge of the punch strip. Default `07:00`. |
| `day_window_end` | `str` "HH:MM" | Right edge of the punch strip. Default `19:00`. |

### `leave_entitlements`

One row per leave year, `days: float` (half-days allowed). Keyed by the calendar
year the leave year *starts* in.

### `clock_events`

Immutable. `action` (`in`/`out`), `timestamp` (tz-aware UTC), `source`
(`user` | `auto` | `import`). Nothing ever updates a clock event; a correction
inserts a new pair and voids the old session.

### `work_sessions`

`clock_in_id`, `clock_out_id` (nullable), `work_date`, `auto_closed`, and — **new
in v2** — `note: str | None` and `voided: bool`. `work_date` is the *local* date
of the clock-in, so a session that runs past midnight belongs to the day it
started.

**A session under `defaults.minimum_session_seconds` never happened.** Clocking
in and straight back out is a slip of the finger, and it is voided rather than
deleted — the events stay, because they are immutable and the audit trail is the
point, but the session is absent from the table and from every figure derived
from it.

### `absence_days`

**Changed in v2.** The old table allowed one whole day per date. It now allows a
morning and an afternoon:

| Column | Type | Meaning |
|---|---|---|
| `date` | `date` | |
| `portion` | `enum` | `full` \| `am` \| `pm` |
| `absence_type` | `enum` | `annual` \| `sick` \| `flexi` \| `unpaid` \| `other` |
| `note` | `str \| None` | Required for `other`, optional elsewhere. |

Unique constraint on `(date, portion)`, plus an application-level rule that a
`full` cannot coexist with an `am` or `pm` on the same date. Two half-days of
*different* types on one date are legal (a sick morning and an annual afternoon)
and the UI must render both.

`portion` values in days: `full` = 1.0, `am` = 0.5, `pm` = 0.5.

### `bank_holiday_cache`

Unchanged: `(division, date)` unique, `title`, `fetched_at`.

---

## 3. Absence types

| Type | Draws down | Counted as | Notes |
|---|---|---|---|
| `annual` | annual entitlement | not worked | Blocked when the remaining balance is smaller than the request. |
| `sick` | nothing | not worked | Counted, never limited. A count and a Bradford-style occurrence tally. |
| `flexi` | the flexi balance | not worked | The withdrawal side of TOIL. Warn — do not block — if it would take the balance negative. |
| `unpaid` | nothing | not worked | Recorded so the day is not read as a no-show. |
| `other` | nothing | not worked | Requires a note. Jury service, moving day, anything the other four do not describe. |

`bank_holiday` is **not** an absence type. It is a property of the date, it comes
from GOV.UK, and it cannot be booked or removed. It is listed alongside absences
everywhere in the UI because that is where a reader looks for it.

### Invariants

- An absence cannot be booked on a non-working day or a bank holiday.
- An absence cannot be booked on a date that already has work sessions, unless
  it is a half-day and the sessions fit in the other half.
- Clocking in on a `full` absence date is refused. Clocking in on a half-day is
  allowed.
- Deleting an absence restores the allowance it drew down.

---

## 4. The period model

the reference application models a period as *an offset from today*, which cannot express next month
— it bells at you instead. Flexi books leave in the future, so it uses an
**anchor**.

```python
class Granularity(StrEnum):
    DAY = "day"; WEEK = "week"; MONTH = "month"; YEAR = "year"

@dataclass(frozen=True, slots=True)
class Period:
    granularity: Granularity
    anchor: date          # any date inside the period

    @property
    def start(self) -> date: ...
    @property
    def end(self) -> date: ...          # inclusive
    def shift(self, n: int) -> Period: ...       # n periods forward/back
    def zoom(self, g: Granularity) -> Period: ...# keep the anchor, change the span
    def contains(self, d: date) -> bool: ...
    def days(self) -> Iterator[date]: ...
    @property
    def label(self) -> str: ...         # "Week of 8 Jun", "June 2026", "2026/27"
    @property
    def is_current(self) -> bool: ...   # contains today
```

Two rules that make the control feel right:

1. **Zooming keeps the anchor.** On Thursday of week 24, pressing `m` gives you
   June — not "month offset 0". Pressing `w` again gives you week 24 back,
   because the anchor never moved.
2. **`t` (today) resets the anchor, not the granularity.** A user who has chosen
   a month view wants *this* month, not this week.

Week starts Monday. `WEEK.label` is `"Week of 8 Jun"`; `YEAR` follows the leave
year, so its label is `"2026/27"` when `leave_year_start` is not `01-01`.

---

## 5. The day ledger

The single view model every widget reads. Computed by
`flexi.services.ledger.LedgerService.day(d)` and cached per rebuild.

```python
@dataclass(frozen=True, slots=True)
class Segment:
    start: datetime          # local
    end: datetime | None     # None while open
    open: bool
    auto_closed: bool
    note: str | None
    session_id: int

@dataclass(frozen=True, slots=True)
class DayLedger:
    date: date
    kind: DayKind            # WORKING | WEEKEND | HOLIDAY | ABSENT | PARTIAL
    holiday_title: str | None
    absences: tuple[AbsenceSlice, ...]   # 0, 1 (full) or up to 2 (am+pm)
    segments: tuple[Segment, ...]
    worked: timedelta
    expected: timedelta
    @property
    def delta(self) -> timedelta:  return self.worked - self.expected
    @property
    def is_open(self) -> bool: ...
```

`DayKind.PARTIAL` is a day with a half-day absence and work in the other half —
the case that makes a naive "one status per day" table wrong, and the reason the
records table has expandable rows.

---

## 6. Migrations

v2 adds three migrations on top of `0005_absence_days`:

| Revision | Change |
|---|---|
| `0006_settings_contracted` | `settings.contracted_minutes`, `day_window_start`, `day_window_end`, backfilled to 444 / 07:00 / 19:00. |
| `0007_absence_portion` | `absence_days.portion` (default `FULL`), `absence_days.note`, and the two new absence types. Moves uniqueness from `date` to `(date, portion)`. |
| `0008_session_note` | `work_sessions.note`, `work_sessions.voided` (default false). |
| `0009_balance_adjustments` | `balance_adjustments`: a signed, dated, reasoned correction. |

`0007` rebuilds the table rather than altering it: the v1 schema put `UNIQUE` on
the `date` column itself and SQLite cannot drop a column constraint in place.
The enum widening rides along in the same rebuild — SQLAlchemy renders an `Enum`
on SQLite as a plain `VARCHAR` with no check constraint, so it needs no storage
change, but writing the new definition out keeps the schema and the model
agreeing on paper as well as in practice.

Its `downgrade` **drops** half-days and the two new types rather than coercing
them. A morning of sickness silently becoming a whole day off is a worse outcome
than losing the row, and a downgrade is a deliberate act rather than an accident.

Every migration is reversible. See [`TESTING.md`](TESTING.md).
