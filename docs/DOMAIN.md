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

The sum restarts at the leave-year boundary. On the 6th of April the balance is
nought again, whatever it was on the 5th; nothing is carried forward.

**Adjustments are the only stored term.** Everything else is derived from clock
events, so there is no total to edit when someone wants to draw a line under a
period they never tracked. `flexi balance zero` writes one signed row instead,
which survives every recomputation and leaves the sessions behind it intact.

It settles to *yesterday* by default. Today is not over, and absorbing its
contracted hours before they have been worked would leave the evening looking
like unearned overtime.

`tracking_since` on the settings row is the other half of the same problem: days
before the day Flexi was set up expect nothing, so installing in November does
not open on seven months of deficit. It is `None` on databases migrated from
before `0015`, and `None` means every day counts.

`expected_hours(d)` is the crux:

| Day | expected |
|---|---|
| Before `tracking_since`, with no punched session on it | `0` |
| Not a working day (per `working_days`) | `0` |
| Bank holiday in the configured division | `0` |
| Whole-day absence of any type | `0` |
| One half-day absence | `contracted_hours / 2` |
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
it. That is why the dashboard refreshes on a timer.

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
| `auto_close_time` | `str` "HH:MM" | A session still open at this time on a later day is closed here, not left running. |
| `contracted_minutes` | `int` | Minutes in a standard working day. Default `444` (7h 24m). |
| `day_window_start` | `str` "HH:MM" | Left edge of the punch strip. Default `07:00`. |
| `day_window_end` | `str` "HH:MM" | Right edge of the punch strip. Default `19:00`. |
| `tracking_since` | `date \| None` | The day setup was answered. Days before it expect no work. `None` (pre-`0015` databases) means every day counts. |

A `CHECK` and a `UNIQUE` on `singleton_key` make this a true single-row table.

### `leave_entitlements`

One row per leave year, `days: float` (half-days allowed). Keyed by the calendar
year the leave year *starts* in.

### `clock_events`

Immutable, and enforced as such: `0012` installs a trigger that rejects any
`UPDATE`. A correction inserts a replacement pair and voids the old session.

| Column | Type | Meaning |
|---|---|---|
| `action` | `enum` | `in` \| `out` |
| `timestamp` | `datetime` | The wall-clock reading, naive. SQLite has no timestamp type, so a tz-aware column stored the field values and dropped the offset. |
| `utc_offset_minutes` | `int \| None` | Minutes east of UTC when the clock was read; the instant is `timestamp` minus this. `None` only on rows written before `0010`. |
| `source` | `str` | `user` \| `system` \| `amended` |

Both halves of the timestamp are needed. The wall reading is the punch strip, the
work date and the midday split; the offset is why 22:00 on 24 October to 06:00 on
25 October is nine hours and not eight.

`source` is a plain `VARCHAR` with no `CHECK`, because `0004` wrote it that way
and `0010` reads it back to decide whose timestamps it may rewrite. A value
outside the three is accepted on write and then raises on every ORM read, so the
vocabulary is a contract even though the column does not enforce it. `system` is
the auto-close sweep; `amended` is work recorded after the fact.

### `work_sessions`

`clock_in_id`, `clock_out_id` (nullable), `work_date`, `auto_closed`,
`note: str | None` and `voided: bool`. `work_date` is the *local* date of the
clock-in, so a session that runs past midnight belongs to the day it started.

A partial unique index allows at most one open, unvoided session at a time, and
`0014` binds each event to one correctly oriented role: a clock-in cannot be
filed as a clock-out.

**A session under `defaults.minimum_session_seconds` never happened.** Clocking
in and straight back out is a slip of the finger, and it is voided, not
deleted — the events stay, because they are immutable and the audit trail is the
point, but the session is absent from the table and from every figure derived
from it. The preference is bounded to 0–3600 seconds and is evaluated only when
the session is closed, so a later config change never rewrites history.

### `absence_days`

| Column | Type | Meaning |
|---|---|---|
| `date` | `date` | |
| `portion` | `enum` | `full` \| `am` \| `pm` |
| `absence_type` | `enum` | `annual` \| `sick` \| `flexi` \| `unpaid` \| `other` |
| `note` | `str \| None` | Required for `other`, optional elsewhere. |

Unique on `(date, portion)`, plus two partial unique indexes that treat `full` as
conflicting once with `am` and once with `pm`. So the useful `am` + `pm` pair is
admitted and every full/half collision is a database error, including a write
that bypasses the service layer. Two half-days of *different* types on one date
are legal — a sick morning and an annual afternoon — and the UI renders both.

`portion` values in days: `full` = 1.0, `am` = 0.5, `pm` = 0.5.

### `bank_holiday_refreshes`, `bank_holiday_cache`, `bank_holiday_attempts`

Freshness belongs to the response, not to each event in it.
`bank_holiday_refreshes` holds one row per division — `division` (the key) and
`fetched_at` — and records that the division was fetched successfully even when
GOV.UK returned no events for it.
`bank_holiday_cache` is one event of that response: `division` (a cascading
foreign key), `date`, `title`, unique on `(division, date)`.

`bank_holiday_attempts` (`division`, `attempted_at`) records a fetch that came
back with nothing usable. It is a separate table because a row in
`bank_holiday_refreshes` claims a complete calendar, and a failed fetch that
claimed one would turn "no calendar at all" into "a year with no bank holidays in
it" — every holiday a working day, quietly.

### `balance_adjustments`

`date` (when the correction takes effect), `minutes` (signed), `reason`,
`created_at`. Written by `flexi balance zero` and by the settle action; removable
by id through `flexi balance undo`.

---

## 3. Absence types

| Type | Draws down | Counted as | Notes |
|---|---|---|---|
| `annual` | annual entitlement | not worked | Blocked when the remaining balance is smaller than the request. |
| `sick` | nothing | not worked | Counted, never limited. Tallied two ways: days (a half-day is 0.5) and occasions, one per booked day or half-day — so a five-day range is five occasions, and a sick morning plus a sick afternoon is two. |
| `flexi` | the flexi balance | not worked | The withdrawal side of TOIL. Warn — do not block — if it would take the balance negative. |
| `unpaid` | nothing | not worked | Recorded so the day is not read as a no-show. |
| `other` | nothing | not worked | Requires a note. Jury service, moving day, anything the other four do not describe. |

`bank_holiday` is **not** an absence type. It is a property of the date, it comes
from GOV.UK, and it cannot be booked or removed. It is listed alongside absences
everywhere in the UI because that is where a reader looks for it.

### Invariants

`verdict_for` in `services/absence.py` decides a booking. It reports the first
objection only, cheapest first:

1. `other` without a note.
2. Not a working day.
3. No bank holiday calendar — so the day cannot be ruled out as one.
4. The day *is* a bank holiday.
5. A clash: the day is booked in full, or half-booked and a full day was asked
   for, or that half is already booked, or there is recorded work in that half.
6. Annual leave beyond what the year has left. No entitlement recorded at all is
   not the same as none left, and refuses nothing.

TOIL is warned about, never refused: a booking that would overdraw the balance
goes in with a warning beside it.

Clocking in is refused on a bank holiday, on a day booked off in full, and during
a booked half — a booked morning leaves the afternoon workable.

Removing an absence restores the allowance it drew down. A booking on a day that
a later change to the working pattern turned into a non-working day stays where
it is and stops counting; it is not deleted behind the user's back.

---

## 4. The period model

A period modelled as *an offset from today* cannot express next month. Flexi
books leave in the future, so it uses an
**anchor**.

```python
class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class Period:
    granularity: Granularity
    anchor: date                          # any date inside the period
    year_start: tuple[int, int] = (1, 1)  # (month, day) the leave year opens on
    first_weekday: int = 0                # Monday

    @classmethod
    def containing(cls, moment: date, granularity: Granularity, ...) -> Period: ...

    @property
    def start(self) -> date: ...
    @property
    def end(self) -> date: ...            # inclusive
    @property
    def label(self) -> str: ...           # "Week of 8 Jun", "June 2026", "2026/27"
    def days(self) -> list[date]: ...
    def contains(self, moment: date) -> bool: ...
    def shift(self, count: int) -> Period: ...
    def zoom(self, granularity: Granularity) -> Period: ...
    def go_to(self, moment: date) -> Period: ...
    def with_year_start(self, year_start: tuple[int, int]) -> Period: ...
```

`year_start` and `first_weekday` travel with the period because they change what
`start` and `end` mean: a `YEAR` runs from the leave-year anniversary, and a
`WEEK` from whichever day the user calls the first.

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
    session_id: int
    start: datetime  # aware
    end: datetime | None = None  # None while the session is open
    auto_closed: bool = False
    amended: bool = False  # recorded after the fact, never punched
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DayLedger:
    date: date
    kind: DayKind  # WORKING WEEKEND HOLIDAY ABSENT PARTIAL UNTRACKED
    is_working_day: bool
    contracted: timedelta
    worked: timedelta
    expected: timedelta
    toil_taken: timedelta = timedelta()
    adjustment: timedelta = timedelta()
    holiday_title: str | None = None
    absences: tuple[AbsenceSlice, ...] = ()  # 0, 1 (full), or 2 (am + pm)
    segments: tuple[Segment, ...] = ()

    @property
    def delta(self) -> timedelta:  # worked − expected
        ...

    @property
    def balance_effect(self) -> timedelta:  # delta − toil_taken + adjustment
        ...
```

`delta` is what the day's `±` column shows. `balance_effect` is what the running
balance accumulates: a TOIL day expects nothing, so it scores no deficit for
being unworked, and it spends a day of the surplus that paid for it.

Every duration that can still be running takes a `now`, so a widget redrawing on
a timer says what the elapsed time is at the moment it draws, and no ledger
reaches for the wall clock itself.

`DayKind.PARTIAL` is a day with a half-day absence and work in the other half —
the case that makes a naive "one status per day" table wrong, and the reason the
records table has expandable rows.

---

## 6. Migrations

Alembic, in `src/flexi/migrations/versions/`, run on every launch with a backup
taken first. Each revision's module docstring says why it exists; this is the
chain.

| Revision | Change |
|---|---|
| `0001_initial` | The migration infrastructure. |
| `0002_settings` | `settings` and `leave_entitlements`. |
| `0003_bank_holiday_cache` | `bank_holiday_cache`. |
| `0004_clock_events_work_sessions` | `clock_events` and `work_sessions`. |
| `0005_absence_days` | `absence_days`, one whole day per date. |
| `0006_settings_contracted` | `contracted_minutes`, `day_window_start`, `day_window_end`, backfilled to 444 / 07:00 / 19:00. |
| `0007_absence_portion` | `absence_days.portion` and `.note`, the two extra absence types, and uniqueness moved from `date` to `(date, portion)`. |
| `0008_session_note` | `work_sessions.note` and `.voided`. |
| `0009_balance_adjustments` | `balance_adjustments`: a signed, dated, reasoned correction. |
| `0010_clock_event_offsets` | `clock_events.utc_offset_minutes`, backfilled for rows Flexi punched itself. |
| `0011_persistence_invariants` | The `settings` singleton key, and the partial indexes that make full/half absence collisions a database error. |
| `0012_clock_event_immutability` | A trigger that rejects any `UPDATE` to a clock event. |
| `0013_bank_holiday_refreshes` | `bank_holiday_refreshes`, so a successful fetch that returned no events is distinguishable from no fetch. |
| `0014_work_session_event_invariants` | Each clock event bound to one correctly oriented work-session role. |
| `0015_settings_tracking_since` | `settings.tracking_since`. Null on an upgraded database, which means every day counts. |
| `0016_bank_holiday_attempts` | `bank_holiday_attempts`, so a failed fetch is not retried once per command all day. |

`0007` rebuilds `absence_days` instead of altering it: the original schema put
`UNIQUE` on the `date` column itself, and SQLite cannot drop a column constraint
in place. The enum widening rides along in the same rebuild — SQLAlchemy renders
an `Enum` on SQLite as a plain `VARCHAR` with no check constraint, so it needs no
storage change, but writing the new definition out keeps the schema and the model
agreeing on paper as well as in practice.

Its `downgrade` **drops** half-days and the two extra types instead of coercing
them. A morning of sickness silently becoming a whole day off is a worse outcome
than losing the row, and a downgrade is an explicit act.

Every migration is reversible, and `tests/models/test_migrations.py` round-trips
the chain against a populated database. See [`TESTING.md`](TESTING.md).
