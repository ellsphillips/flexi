# flexi·

A terminal application for tracking flexitime. When you were on the clock, how
far ahead or behind your contracted hours you are, and what is left in your leave
allowances.

```
 ╭─ Clock ───────────────────────╮ ╭─ Records ────────────────────────────────────────────────────────────────────────╮
 │ on the clock                  │ │  Day                                                        Worked   ±           │
 │ ───██████████████▌─┊────      │ │  Mon 08   ────██████████████████··████████████████────────     8:13   +0:49      │
 │ since 08:44 · go home 16:48   │ │  Tue 09   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓────████████████████████████     7:36   +3:54      │
 │                               │ │  Wed 10   ───────███████████████··████████████████────────     7:32   +0:08      │
 │            Depart             │ │  Thu 11   ──────█████████████████··█████████▌────┊────────     6:08   −1:16      │
 ╰───────────────────── 6:08:00 ─╯ │  Fri 12   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓     TOIL              │
 ╭─ Balance ─────────────────────╮ │  Sat 13   ────────────────────────────────────────────────        —              │
 │    ╶─╮╭─╮   ╷ ╷╭─╮            │ │  Sun 14   ────────────────────────────────────────────────        —              │
 │ ╶┼╴┌─┘│ │ : ╰─┤├─┤            │ │  Week                                                         29:29   −3:49      │
 │    ╰─╴╰─╯     ╵╰─╯            │ ╰──────────────────────────────────────────────────── 29:29 of 25:54 ─╯
 │         FLEXI BALANCE         │
 │   20:48 banked · +2.8 days    │
 ╰─────────── 6 Apr 26–5 Apr 27 ─╯
```

## Try it

```
uv run flexi --demo
```

A throwaway database seeded with a leave year of plausible records — some
overtime, a short day, a week of annual leave, two bank holidays, a sick day, a
half-day and a TOIL day. Deleted on exit, so it can never be confused with your
own.

Then, for real:

```
uv tool install flexi
flexi
```

## What it does

**Clock in and out with one key.** `/`, from anywhere, at any time. Nothing to
confirm — clock events are immutable and a second `/` opens a new session, so a
mistaken press costs one visible break, and the status bar says what was recorded
and at what time.

**Draws the day as a punch strip.** One row of cells across the working day,
filled where you were on the clock, with a tick where your contracted hours will
have been met. Seven of them on a shared time axis and the shape of your week is
legible at a glance.

**Tracks every kind of absence.** Annual leave, sickness, TOIL, unpaid and other,
in whole days or mornings and afternoons — a sick morning and an annual afternoon
can share a date, because that happens. Each allowance is drawn against a marker
showing where an even spread through the leave year would have put you.

**Moves through time.** Day, week, month or leave year; `[` and `]` to step, `t`
for today, `g` to go to a date typed however is quickest — `12`, `12 Jun`,
`2026-06-12`, `+3d`. Next month is reachable, because leave is booked in advance.

**Opens any day.** `space` on a row shows the sessions, the breaks between them,
what the day expected and what it got.

**Jumps anywhere.** `v` puts a one-key badge on every panel and on the first nine
day rows. Press the key, land there; `escape` puts you back exactly where you
were.

**Shows how you got here.** `f2` for the leave year: the balance week by week,
annual leave against its pace line, the shape of the last three weeks, and every
day of the year on a diverging heatmap.

Press `?` for the whole keyboard, or `ctrl+p` for everything that never earned a
key.

## Documentation

Everything is in [`docs/`](docs/) — the domain model and the arithmetic of a
flexi balance, the design system, the architecture, the keymap, the testing
strategy and the roadmap. Start with [`docs/README.md`](docs/README.md).

## Development

```
uv sync
uv run pytest -q                # ~300 tests, about 30 seconds
uv run mypy                     # strict, and meant to stay strict
uv run ruff check src tests
uv run python scripts/shoot.py  # regenerate the screenshots in docs/shots/
```

Flexi owes a great deal to [the reference application](), which
is where jump mode, the module container and much of the scaffolding come from.
