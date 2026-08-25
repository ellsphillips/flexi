# Roadmap

Eleven slices. Each is independently shippable, leaves the test suite green, and
has acceptance criteria you can check without reading the diff. Work them in
order — later slices assume the earlier ones.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done

Slices 1–11 are done. Deviations from the plan as written are recorded
under each slice; the plan was not retro-fitted to the code.

---

## Phase 1 — Foundations

### `[x]` 1. Upgrade and re-plumb

Textual `1.0.0` → `8.2.x`; add `pydantic`, `pyyaml`, `time-machine`,
`pytest-textual-snapshot`. Add `flexi/config.py` and the `Services` registry.
Delete `util/style.py` (its `load_css` glob is dead code — the `if not
stylesheet` check on a generator is always false and it hardcodes `style.scss`
anyway).

**Done when:** the existing 111 tests pass on Textual 8, `flexi` launches,
`tests/test_layering.py` exists and passes.

### `[x]` 2. The domain package

`domain/period.py`, `domain/ledger.py`, `domain/balance.py`, `domain/format.py`.
Move the arithmetic out of `WalletService`, and out of the private helpers
currently duplicated in `day_table` and `wallet` (`_session_seconds`,
`_session_hours`, `_fmt_hours`, `_fmt_duration` are four functions computing two
things).

**Done when:** `Period` passes its full matrix; `flexi_balance()` is tested
against a hand-worked six-week fixture; `domain/` imports neither Textual nor
SQLAlchemy.

### `[x]` 3. Schema

Migrations `0006`–`0009` from [`DOMAIN.md`](DOMAIN.md) §6: contracted minutes and
day window on `settings`; `portion` and `note` on `absence_days`; `note` and
`voided` on `work_sessions`; the widened absence enum. Extend `AbsenceService` to
half-days and the two new types.

**Done when:** upgrade/downgrade round-trips against a populated database; an AM
and a PM absence of different types coexist on one date; booking a full day over
either is refused.

*Landed as three migrations, not four:* the enum widening is a `VARCHAR` no-op on
SQLite and rides along in `0007`'s table rebuild rather than earning its own
revision. See [`DOMAIN.md`](DOMAIN.md) §6.

### `[x]` 4. Theme and chrome

`theme/flexi.tcss` with the validated palette, `theme/__init__.py` with the
`palette()` parser, `styles/*.tcss`. Port `common.py` (`Tone`, `Pill`,
`StatCard`, `KeyHint`, `Rule`, `Gauge`) and `chrome.py` (`Lockup`, `NavBar`,
`AppHeader`, `StatusBar`, `KeyStrip`, `AppFooter`).

**Done when:** `KeyStrip` shows `+n more` at 80 columns rather than a half-drawn
key, and its two measurement functions are tested directly; no hardcoded hex
remains outside the `PALETTE` block (`grep -rn '#[0-9a-fA-F]\{6\}' src/flexi
--include=*.py --include=*.tcss` returns only `flexi.tcss` and the fallback table
in `theme/__init__.py`, which exists for the case where the stylesheet cannot be
read at all).

---

## Phase 2 — The six features

### `[x]` 5. Clock — *feature 1*

`ClockModule`: state pill, a `Switch` and two buttons, the live elapsed subtitle,
the one-second tick. `/` bound at app level with `priority=True`. Early-departure
confirmation. Status-bar reporting of every result.

**Done when:** `/` works from all three screens and is swallowed by a focused
`Input`; clocking in twice is refused with a message, not an exception; the
elapsed time advances every second and the timer is torn down on clock-out.

### `[x]` 6. Punch strip — *the signature*

`domain/punch.py` (pure bucketing) and `components/punch.py` (`PunchStrip`).
Adaptive resolution, the seven glyph states, the contracted-hours tick, the live
edge.

**Done when:** bucketing is pinned by exact-string tests at widths 80, 48 and 20;
a day with three sessions and a half-day absence renders correctly; the strip
never exceeds the width it is given.

### `[x]` 7. Records table — *feature 3*

`ExpandableTable` over the stock `DataTable`, `RecordsModule`, the row-key
scheme, the child rows, the period footer row. `LedgerService` with per-rebuild
memoisation.

**Done when:** `space` expands without moving the cursor; a month view issues one
pass over the period rather than a query per day (assert the query count with an
`event.listen` counter); the table redraws inside the one-second tick budget at
31 rows.

*Deviation:* strips are painted into cells with `render_strip` rather than
mounted as `PunchStrip` widgets — thirty-one widgets would cost a layout pass per
redraw on the one widget that redraws every second. `RecordsModule` therefore
declares the punch component classes itself; component classes are scoped to the
widget type that declares them, so `PunchStrip`'s rules do not reach its cells.

### `[x]` 8. Calendar — *feature 4*

`MonthView` on the new `Period` model: `d`/`w`/`m`/`y`, `[`/`]`, `t`, `g`,
`,`/`.`. Day-type markers from the validated scale. The current period is
indicated by a tinted row (week), a tinted block (month) and a ring on today —
today and *selected* must be distinguishable when they are different days.

**Done when:** zooming keeps the anchor (Thursday → `m` → June → `w` → that same
week); the period label reads `Week of 8 Jun` / `June 2026` / `2026/27`; future
periods are reachable (the v1 code bells instead).

### `[x]` 9. Wallet — *feature 2*

`WalletModule`: a `Gauge` per allowance — annual drawn down against entitlement,
TOIL against the accrued balance, sick and unpaid as counts with occurrence
tallies. The five shifted booking keys. The absence modal (type, date or range,
portion, note) with validation surfaced inline.

**Done when:** booking annual leave that exceeds the remaining balance is
refused with the shortfall named; a TOIL day that would take the balance
negative warns but proceeds; deleting an absence restores the allowance; the
gauges agree with `tests/services/test_wallet.py`.

### `[x]` 10. Jump mode and the keyboard — *features 5 and 6*

`jumper.py`, `jump_overlay.py`, `jump.tcss`, `jump_targets()` on every screen,
row targets in the records table. The help screen. `FlexiCommands` provider.
`test_bindings.py` and `test_modal_contract.py`.

**Done when:** `v` then a panel key focuses that panel and `escape` restores the
previous focus exactly; `v` then `4` lands on the fourth day row; every action
named by a binding resolves; `?` lists every binding including those the strip
dropped.

*Two things the first screenshots caught:* a jump to a module landed on the panel
rather than on its table, so `Module.focus_target()` now names what a jump should
focus; and the five booking keys were declared on the wallet, where they only
fired when the wallet had focus — they belong on the screen, as this document
already said.

---

## Phase 3 — Beyond the brief

### `[x]` 11. Insights

Only after 1–10 are green and screenshotted. The charts are earned by the data
model, not bolted on:

- **Balance over time.** A line of cumulative flexi balance across the leave
  year, zero-baselined, with the surplus above and deficit below in the two
  reserved state colours. This is the app's history in one picture.
- **Allowance burn-down.** Annual leave remaining against a straight-line pace
  line, so "am I banking leave I will lose?" is answerable at a glance. One
  series plus a reference line — no legend needed, the title names it.
- **Week shape.** A small-multiples grid of punch strips, one row per week of the
  period, on a shared time axis. The signature element, scaled up.
- **Day-of-week profile.** Median start, median end and median worked per
  weekday. Answers "are my Fridays actually short?"
- **Year heatmap.** A calendar grid coloured by the day's delta on a *diverging*
  ramp — surplus and deficit poles, neutral grey midpoint — with day type shown
  by the glyph, not the colour, so the two encodings do not fight.

*Landed as four charts, all hand-drawn.* The day-of-week profile is not built;
the week ribbon answers most of what it would have. `plotext` went unused —
every form here is a few dozen characters wide and drawing them directly gave
control over the palette and the glyph set that a plotting library would have
taken back. It stays in the dependencies for the day a real line chart is wanted.

*Two things worth knowing before adding a fifth chart:*

- **Whole cells, both arms.** Eighths read beautifully upward (`▁▂▃▄▅▆▇`) and
  need U+1FB0x Symbols for Legacy Computing to do the same downward. Those are
  missing from most terminal fonts, so half the first draft rendered as tofu.
- **A chart stops at today.** Every working day after it expects hours and has
  none recorded, so charting the rest of a leave year draws a cliff of deficits
  for days nobody has lived yet.

Chart rules, unchanged: **series colours come from the three validated slots
only** (`$c-toil`, `$c-annual`, `$c-sick`); a fourth series folds into a neutral
"Other". Sequential ramps are one hue light→dark; the diverging heatmap is two
poles with a grey midpoint and never a rainbow. Its eight steps were generated
as fixed OKLCH hues at rising lightness and validated for monotonic steps and
surface contrast — re-run `scripts/validate_palette.js --ordinal` per arm if any
of that changes.

### Later, unscheduled

- `flexi export --csv --from --to` and an ICS feed of booked absence.
- A `doctor` command: database integrity, orphaned sessions, bank-holiday cache
  age, config validation.
- Import from a CSV timesheet.
- Multi-day absence booking by dragging a range in the calendar.
- Notification on approaching a leave-year end with unspent allowance.

---

## Definition of done, for every slice

1. Tests for the kinds the slice touches (see [`TESTING.md`](TESTING.md)).
2. Snapshots at 120×34, 84×26 and 64×20, reviewed as images.
3. `mypy --strict src` and `ruff check` clean.
4. The relevant document in `docs/` updated in the same commit — if the slice
   changed a binding, `KEYMAP.md` changes with it.
5. No new hardcoded colour, no new private-attribute reach into the app, no new
   `# type: ignore` without a comment saying why.
