# ⏱️ flexi

A terminal timesheet for people on flexitime. It knows what time off in lieu is, and that your leave year probably starts in April.

[![version](https://shieldcn.dev/badge/version-0.2.0-00AAAD.svg?variant=outline)](https://pypi.org/project/flexi/)
[![python](https://shieldcn.dev/badge/python-3.12_|_3.13_|_3.14-00AAAD.svg?logo=python&variant=outline)](https://www.python.org)
[![textual](https://shieldcn.dev/badge/tui-textual-00AAAD.svg?logo=textual&variant=outline)](https://textual.textualize.io)
[![sqlite](https://shieldcn.dev/badge/storage-sqlite-00AAAD.svg?logo=sqlite&variant=outline)](https://www.sqlite.org)
[![uv](https://shieldcn.dev/badge/packaging-uv-00AAAD.svg?logo=uv&variant=outline)](https://docs.astral.sh/uv/)

[![ci](https://shieldcn.dev/github/ci/ellsphillips/flexi.svg?variant=outline)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![mypy](https://shieldcn.dev/badge/mypy-strict-2E9E52.svg)](https://mypy-lang.org)
[![ruff](https://shieldcn.dev/badge/ruff-select_ALL-2E9E52.svg?logo=ruff)](https://github.com/astral-sh/ruff)
[![licence](https://shieldcn.dev/badge/licence-MIT-2E9E52.svg)](https://github.com/ellsphillips/flexi/blob/main/LICENSE)
[![prs](https://shieldcn.dev/badge/PRs-welcome-2E9E52.svg?variant=outline)](https://github.com/ellsphillips/flexi/blob/main/CONTRIBUTING.md)

![The dashboard](https://raw.githubusercontent.com/ellsphillips/flexi/main/docs/shots/showcase-dashboard.svg)

TOIL comes out of the same balance overtime goes into. The leave year starts on the 6th of April, or wherever you put it. Bank holidays come from GOV.UK, so annual leave cannot be booked on one. Records live in one SQLite file on your own disk: no account, no server.

With [uv](https://docs.astral.sh/uv/), look around without installing anything. Needs Python 3.12 or newer, and a terminal to draw on.

```bash
uvx flexi --demo
```

## A look around

### On the clock

Press `/`. You are on the clock; press it again and you are off. A session left running past midnight is closed at the auto-close time you chose.

Each day is a punch strip: cells across the working day, filled where you were on the clock, with a tick where the contracted hours are met. Seven on one axis is a week at a glance.

Absence comes in five kinds — annual, sick, TOIL, unpaid, other — whole or half a day, and two halves can share a date.

### Work you never clocked

Some mornings you forget to press `/`. `n` records the stretch whole. It counts for everything a punched session counts for, and is drawn filled instead of solid so the two are never confused. `N` lists the period's corrections.

### Records that open

`space` on a day opens the sessions behind the figure, the breaks between them, and how the total compares to what the day expected.

![Records, expanded](https://raw.githubusercontent.com/ellsphillips/flexi/main/docs/shots/showcase-records.svg)

### The leave year

`f2` opens the year as one scrolling grid, months stitched together, so a fortnight across the end of July looks like one. Put the cursor on a day and press `A`: it is annual leave. `shift` and an arrow extends the selection; `space` cycles a cell between whole day, morning and afternoon. Book a fortnight over a bank holiday and nine days go in, with the five it skipped named.

![The leave year](https://raw.githubusercontent.com/ellsphillips/flexi/main/docs/shots/showcase-leave.svg)

Colour carries the type and the glyph carries the portion; both are spelled out in words beside the grid.

![annual](https://shieldcn.dev/badge/●-annual-8451C9.svg)
![sick](https://shieldcn.dev/badge/●-sick-DB703B.svg)
![toil](https://shieldcn.dev/badge/●-TOIL-00AAAD.svg)
![unpaid](https://shieldcn.dev/badge/●-unpaid-8B7E6D.svg)
![other](https://shieldcn.dev/badge/●-other-BE5BAC.svg)
![bank holiday](https://shieldcn.dev/badge/●-bank_holiday-97B1CD.svg)
![surplus](https://shieldcn.dev/badge/●-surplus-2E9E52.svg)
![deficit](https://shieldcn.dev/badge/●-deficit-CE3E5D.svg)

### Where the balance went

`f3` is the year in five panels: the running balance, the balance week by week, annual leave against the pace that would spend it, recent weeks side by side, and the year on one heatmap.

![Insights](https://raw.githubusercontent.com/ellsphillips/flexi/main/docs/shots/showcase-insights.svg)

## What it will not do

- Teams. One person, one database, no manager view.
- Invoicing. It measures time against a contract, not against a rate.
- Bank holidays outside the UK. It knows the three GOV.UK divisions and nothing else, so you book those days yourself.
- Automatic tracking. It does not watch your keyboard, your calendar or your repositories.
- Payroll. This is your record, to check theirs against.
- A contract other than 37 hours. A day is seven hours twenty-four minutes with nowhere yet to say otherwise, so a 35- or 37.5-hour week drifts by the difference every day you work.
- Carry a balance between leave years. The flexi balance is the sum for the current one, so in April it starts again from nought.

## Install

Python 3.12, 3.13 or 3.14, on macOS, Linux or Windows. Every release runs the suite on all three platforms, on each interpreter, under UTC, Europe/London and America/New_York.

It draws with box-drawing and block characters. [Ghostty](https://ghostty.org), [WezTerm](https://wezterm.org), [Kitty](https://sw.kovidgoyal.net/kitty/) and [Alacritty](https://alacritty.org) render it as shown; Terminal.app flattens the colours; on Windows use [Windows Terminal](https://aka.ms/terminal).

**Recommended.** [uv](https://docs.astral.sh/uv/) gives Flexi its own environment and puts `flexi` on your `PATH`.

```bash
# install uv, if you do not have it
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env        # or restart your shell

uv tool install flexi
flexi
```

On Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# open a new terminal, then
uv tool install flexi
flexi
```

Later: `uv tool upgrade flexi`, `uv tool uninstall flexi`. If your shell cannot find `flexi`, run `uv tool update-shell`.

<details>
    <summary>With pipx</summary>

```bash
pipx install flexi
```

</details>

<details>
    <summary>With pip</summary>

Only if you know which environment you are installing into.

```bash
pip install flexi
```

</details>

<details>
    <summary>From source</summary>

```bash
git clone https://github.com/ellsphillips/flexi
cd flexi
uv sync
uv run flexi
```

</details>

## First run

Five questions, then it gets out of the way.

| | |
|---|---|
| Leave year starts | `04-06` for the 6th of April, the common one |
| Annual entitlement | Your leave in days, halves allowed |
| Working days | `Mon-Fri`, or `Tue, Thu` if you work part time |
| Bank holidays | England & Wales, Scotland, or Northern Ireland |
| Auto-close at | When a session left open overnight is stamped as ending |

Flexi stamps the day you set it up and expects nothing of the days before it, so installing in November does not open you on seven months of deficit.

Entitlement is held per leave year: `f4` lists the years, edits any, and adds the next. A new one has no allowance until you give it one.

`flexi init` asks the five again. Where records exist it says what is there, then offers to open Flexi, change settings, or start over.

## Keys

Everywhere:

| | |
|---|---|
| `/` | Clock in, or out |
| `f1` `f2` `f3` `f4` | Dashboard, Leave, Insights, Settings |
| `v` · `?` · `ctrl+p` | Jump mode · every key · the command palette |

On the dashboard:

| | |
|---|---|
| `d` `w` `m` `y` · `p` | A day, a week, a month, the leave year · cycle |
| `[` `]` · `t` · `g` | Step back and forward · today · go to a date |
| `space` | Open a day to its sessions |
| `n` · `N` | Record work you never clocked · list the corrections |
| `A` `S` `T` `U` `O` | Book annual, sick, TOIL, unpaid, other on the selected day |
| `a` · `x` | Book on the day under the table cursor · remove a booking |

Insights takes `[` `]`, `t` and `p`; the leave year takes the five booking keys on whatever the selection covers. Every table is in [`docs/KEYMAP.md`](https://github.com/ellsphillips/flexi/blob/main/docs/KEYMAP.md).

Dates are typed however is quickest: `12`, `12 Jun`, `2026-06-12`, `+3d`, `-2w`.

![Jump mode](https://raw.githubusercontent.com/ellsphillips/flexi/main/docs/shots/showcase-jump.svg)

## From the shell

```bash
flexi clock in                      # and `clock out`
flexi leave annual friday           # book leave in one line
flexi leave annual mon to fri       # or a whole week
flexi leave sick today pm           # or half a day
flexi leave cancel next monday      # and take it back
flexi balance show                  # where you stand
flexi balance zero --reason "..."   # draw a line and start from nought
flexi balance log                   # every correction, and `undo <id>`
flexi holidays refresh              # when GOV.UK was unreachable
```

`flexi leave` prints the plan and asks first, naming every weekend and bank holiday it skipped. `--dry-run` stops at the plan, `--yes` skips the question, and a declined confirmation exits 1.

There is no field for "I am already plus fourteen hours": put the days in with `n`, or start from nought with `flexi balance zero`. On Windows, set `PYTHONUTF8=1` before redirecting output to a file; the ANSI code page cannot encode a deficit's minus sign.

## Your data

One SQLite file, migrated forward on launch with a backup taken first.

```
~/.local/share/flexi/db.db          # your records
~/.local/share/flexi/db.db.lock     # never removed; stops two copies migrating at once
~/.local/share/flexi/backups/       # snapshots, see below
~/.config/flexi/config.yaml         # optional, hand-written: keybindings and defaults
```

On Windows those are `%LOCALAPPDATA%\flexi\` and `%APPDATA%\flexi\config.yaml`. `XDG_DATA_HOME` and `XDG_CONFIG_HOME` win when they hold an *absolute* path, Windows included; a relative one is ignored, as the XDG specification requires. Flexi never writes `config.yaml`, and a section it cannot parse falls back to that section's defaults silently. [`docs/KEYMAP.md`](https://github.com/ellsphillips/flexi/blob/main/docs/KEYMAP.md) lists every key it takes.

Uninstalling removes the program, not the records: delete `~/.local/share/flexi` and `~/.config/flexi` yourself.

**Restoring a backup.** Every `.bak` is a complete SQLite database. Quit Flexi everywhere, copy the one you want over `db.db`, and launch; an older snapshot migrates forward, with a fresh backup taken first. `db_*.bak` is written before each migration, newest ten kept; `pre-init_*.bak` before a reset, never aged out.

Starting over — `flexi init` where records exist — is the only thing here that loses data, so it is not a flag. It says how many records it would erase, writes a protected snapshot, then asks you to type a word. With no terminal attached there is no menu at all.

Two network calls, neither of which sends anything. The PyPI version check is silent. The bank holidays are not: Flexi fetches them from GOV.UK on first run, caches them for a week, and refuses to book absence until it has them.

## Development

Clone, `uv sync`, `uv run pre-commit install`, then:

```bash
uv run pytest -q                    # the suite, under a minute
uv run mypy                         # strict, over src and tests
uv run ruff check
uv run python scripts/shoot.py      # regenerate the screenshots above
```

Three rules hold the codebase together: `flexi.domain` imports neither Textual nor SQLAlchemy, the system clock is read in one module, and durations are `timedelta`, never float hours.

[`docs/`](https://github.com/ellsphillips/flexi/tree/main/docs) covers the architecture, the domain model, the design system and the keymap. [`CONTRIBUTING.md`](https://github.com/ellsphillips/flexi/blob/main/CONTRIBUTING.md) says what a change comes with, and [`CHANGELOG.md`](https://github.com/ellsphillips/flexi/blob/main/CHANGELOG.md) what shipped when.

## Licence

[MIT](https://github.com/ellsphillips/flexi/blob/main/LICENSE) © Elliott Phillips
