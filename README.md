<div align="center">

# flexi·

**Flexitime, tracked from the terminal.**

When you were on the clock, how far ahead or behind your contracted hours you
are, and what is left in your leave allowances.

[![CI](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml/badge.svg)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](https://github.com/ellsphillips/flexi)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2)](https://mypy-lang.org/)

</div>

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

## Install

Requires Python 3.12 or later, on macOS or Linux.

```bash
uv tool install flexi
flexi
```

<details>
<summary>Other installers</summary>

```bash
pipx install flexi     # pipx
pip install flexi      # into the current environment
```

</details>

First launch asks four questions — when your leave year starts, which days you
work, which bank holidays apply, and when an open session should auto-close.
Everything after that is keyboard.

To see it working against a leave year of plausible records before committing
your own, `flexi --demo` runs against a throwaway database that is deleted on
exit.

## Usage

| | |
|---|---|
| `/` | Clock in, or out. From anywhere, at any time. |
| `f1` `f2` `f3` `f4` | Dashboard, Leave, Insights, Settings |
| `[` `]` · `t` · `g` | Step back and forward · today · go to a date |
| `d` `w` `m` `y` | Day, week, month or leave year |
| `space` | Open a day to its sessions and breaks |
| `v` | Jump mode: one key to every panel and the first nine rows |
| `?` · `ctrl+p` | The whole keyboard · everything that never earned a key |

**Clocking in takes one key and asks nothing.** Clock events are immutable and a
second `/` opens a new session, so a mistaken press costs one visible break — and
the status bar says what was recorded and at what time.

**The day is drawn as a punch strip.** One row of cells across the working day,
filled where you were on the clock, with a tick where your contracted hours will
have been met. Seven of them on a shared time axis and the shape of your week is
legible at a glance.

**Every kind of absence is tracked.** Annual leave, sickness, TOIL, unpaid and
other, in whole days or mornings and afternoons — a sick morning and an annual
afternoon can share a date, because that happens. Each allowance is drawn against
a marker showing where an even spread through the leave year would have put you.

**Leave is booked on a whole year at once.** `f2` opens the leave year as one
scrolling grid, months stitched together so a fortnight spanning July reads as a
fortnight. `A` books annual leave on the cursor, `shift`+arrows extend to a
range, `space` cycles to half-days, `x` removes. A fortnight across a bank
holiday books twelve days and says so, rather than refusing all fourteen.

**Next month is reachable**, because leave is booked in advance — a period is an
anchor and a granularity, not an offset from today.

There is a small CLI for the things a TUI is the wrong shape for:

```bash
flexi clock in                      # and `clock out`
flexi balance show                  # where you stand
flexi balance zero --reason "..."   # settle a stretch that was never tracked
flexi balance log                   # every adjustment, and `undo <id>`
```

## Where your data lives

One SQLite database under the XDG base directories, migrated forward on launch
with a backup taken first:

```
~/.local/share/flexi/db.db          # records
~/.local/share/flexi/backups/       # the last ten, taken before each migration
~/.config/flexi/config.yaml         # keybindings and defaults
```

Nothing is sent anywhere. The one network call Flexi makes is an update check
against PyPI, and it fails silently.

## Documentation

Everything is in [`docs/`](docs/) — the domain model and the arithmetic of a
flexi balance, the design system, the architecture, the keymap, the testing
strategy and the roadmap. Start with [`docs/README.md`](docs/README.md).

## Contributing

Issues and pull requests are welcome. [`CONTRIBUTING.md`](CONTRIBUTING.md) covers
the layout, the rules the test suite enforces, and what a change is expected to
come with.

```bash
git clone https://github.com/ellsphillips/flexi
cd flexi
uv sync
uv run pre-commit install
uv run pytest -q
```

`ruff` runs with `select = ALL` and a short ignore list, each entry carrying its
reason. `mypy --strict` covers the source and the tests both. The pre-commit
hooks are the same commands CI runs, through the same locked environment.

## Licence

[MIT](LICENSE) © Elliott Phillips
