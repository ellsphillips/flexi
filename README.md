# flexi·

[![CI](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml/badge.svg)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](https://github.com/ellsphillips/flexi)
[![License: MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2)](https://mypy-lang.org/)

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

**Books leave on a whole year at once.** `f2` opens the leave year as one
scrolling grid, months stitched together so a fortnight spanning July reads as a
fortnight. `A` books annual leave on the cursor, `shift`+arrows extend it to a
range, `space` cycles to half-days, `x` removes. A fortnight across a bank
holiday books twelve days and says so, rather than refusing all fourteen.

**Shows how you got here.** `f3` for the leave year in charts: the balance week
by week, annual leave against its pace line, the shape of the last three weeks,
and every day of the year on a diverging heatmap.

Press `?` for the whole keyboard, or `ctrl+p` for everything that never earned a
key.

## Documentation

Everything is in [`docs/`](docs/) — the domain model and the arithmetic of a
flexi balance, the design system, the architecture, the keymap, the testing
strategy and the roadmap. Start with [`docs/README.md`](docs/README.md).

## Development

```
uv sync
uv run pre-commit install       # the hooks are the same commands CI runs

uv run pytest -q                # 438 tests, about a minute
uv run mypy                     # strict, over src and tests both
uv run ruff check
uv run python scripts/shoot.py  # regenerate the screenshots in docs/shots/
```
`ruff` runs with `select = ALL` and a short ignore list, each entry carrying its
reason. Every wide signature is keyword-only, every module reads the system
clock through `flexi.wallclock`, and `flexi.domain` imports neither Textual nor
SQLAlchemy — a test enforces the last of those.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the layout and what a change is
expected to come with.

## Platforms

macOS and Linux. Flexi keeps its database under the XDG base directories and has
no Windows path story yet, so CI does not claim one.
