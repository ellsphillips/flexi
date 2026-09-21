# ⏱️ flexi

Track working hours, leave, and your flexitime balance from the terminal.
Your records stay in a local SQLite database. No account required.

[![version](https://shieldcn.dev/badge/version-0.2.0-00AAAD.svg?variant=outline)](https://pypi.org/project/flexi/)
[![python](https://shieldcn.dev/badge/python-3.12_|_3.13_|_3.14-00AAAD.svg?logo=python&variant=outline)](https://www.python.org)
[![ci](https://shieldcn.dev/github/ci/ellsphillips/flexi.svg?variant=outline)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![licence](https://shieldcn.dev/badge/licence-MIT-2E9E52.svg)](https://github.com/ellsphillips/flexi/blob/main/LICENSE)

## Install

Requires Python 3.12 or newer. Tested on Python 3.12–3.14 with macOS, Linux,
and Windows. Use a terminal with Unicode and colour support; on Windows,
[Windows Terminal](https://aka.ms/terminal) is recommended.

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install "flexi>=0.2.0"
flexi
```

Try it with sample data:

```bash
uvx --from "flexi>=0.2.0" flexi --demo
```

The demo uses a temporary database and removes it on exit. To update an installed
copy, run `uv tool upgrade flexi`. If your shell cannot find the command, run
`uv tool update-shell` and restart the terminal.

<details>
<summary>Other installation methods</summary>

With pipx:

```bash
pipx install "flexi>=0.2.0"
```

With pip, inside an existing virtual environment:

```bash
python -m pip install "flexi>=0.2.0"
```

From source:

```bash
git clone https://github.com/ellsphillips/flexi
cd flexi
uv sync
uv run flexi
```

</details>

## First run

Flexi asks for your leave-year start, annual entitlement, working days,
UK bank-holiday division, and the time to close sessions left running overnight.
Tracking starts that day; earlier days do not create a deficit.

Press `f4` to change settings or set each leave year's entitlement.
A new leave year needs its own allowance.

The interface currently uses **7 hours 24 minutes per working day**. The flexi
balance restarts each leave year. Custom contracted hours, automatic balance
carry, and import/export are not available yet.

## Working with Flexi

![The dashboard](https://raw.githubusercontent.com/ellsphillips/flexi/main/docs/shots/showcase-dashboard.svg)

- **Clock in and out with `/`.** The dashboard shows worked time, expected hours,
  breaks, and your running balance.
- **Add missed work with `n`.** Enter a completed session; overlapping work is
  refused. `N` lists these corrections.
- **Inspect a day with `space`.** Expand its sessions, absences, and balance.
- **Book leave with `f2`.** Annual, sick, TOIL, unpaid, or other absence, in whole
  or half days. Extend a selection with `shift` and an arrow.
- **Review trends with `f3`.** See running balances, weekly comparisons, leave
  usage, and the year at a glance.

![The leave year](https://raw.githubusercontent.com/ellsphillips/flexi/main/docs/shots/showcase-leave.svg)

TOIL draws from your flexi balance. Working-day rules and GOV.UK bank holidays
are checked before booking. Two different half-day absences can share a date.

| Key | Action |
|---|---|
| `f1` · `f2` · `f3` · `f4` | Dashboard · Leave · Insights · Settings |
| `/` | Clock in or out |
| `d` · `w` · `m` · `y` | Day · week · month · leave year on the dashboard |
| `[` · `]` · `t` · `g` | Previous period · next period · today · go to date |
| `A` · `S` · `T` · `U` · `O` | Annual · sick · TOIL · unpaid · other leave |
| `v` · `?` · `ctrl+p` | Jump mode · help · command palette |

The [keymap](https://github.com/ellsphillips/flexi/blob/main/docs/KEYMAP.md)
lists every shortcut and explains remapping. Dates accept forms such as `12`,
`12 Jun`, `2026-06-12`, `+3d`, and `-2w`.

## From the shell

```bash
flexi clock in
flexi clock out
flexi leave annual mon to fri
flexi leave sick today pm
flexi leave cancel next monday
flexi balance show
flexi balance zero --reason "Balance agreed with my manager"
flexi balance log
flexi balance undo 3
flexi holidays refresh
```

Leave commands show a plan and ask before writing. `--dry-run` previews it;
`--yes` skips confirmation. Declining exits with status 1.

`balance zero` settles through yesterday by default. If work overlaps booked
leave, clock-out asks you to remove the conflicting booking and retry; the
session stays open. Clock records are retained as an audit trail.

## Your data

| Platform | Records | Optional preferences |
|---|---|---|
| macOS / Linux | `~/.local/share/flexi/db.db` | `~/.config/flexi/config.yaml` |
| Windows | `%LOCALAPPDATA%\flexi\db.db` | `%APPDATA%\flexi\config.yaml` |

Absolute `XDG_DATA_HOME` and `XDG_CONFIG_HOME` values override these locations.
Flexi never writes the preferences file and reports invalid settings at startup.
Uninstalling the app leaves your records intact.

**Backups are automatic.** Schema upgrades take a snapshot first and retain the
newest ten in the data directory's `backups` folder. Resetting with `flexi init`
asks for confirmation, then writes and verifies a protected backup before
removing all records. Reset backups are kept until you remove them.

**To restore:** close every copy of Flexi, copy a `.bak` file over `db.db`, and
start Flexi. An older schema is upgraded automatically, with another backup first.

Flexi fetches UK bank holidays from GOV.UK and checks PyPI for updates. Neither
request includes your timesheet or settings. Records and backups are unencrypted;
see the [security policy](https://github.com/ellsphillips/flexi/blob/main/SECURITY.md)
for details and vulnerability reporting.

## Development

```bash
uv sync
uv run pre-commit install
uv run pytest -q
uv run mypy
uv run ruff check
```

See [Contributing](https://github.com/ellsphillips/flexi/blob/main/CONTRIBUTING.md)
for the workflow, [the documentation](https://github.com/ellsphillips/flexi/tree/main/docs)
for architecture and testing, and [the changelog](https://github.com/ellsphillips/flexi/blob/main/CHANGELOG.md)
for release notes.

## Licence

[MIT](https://github.com/ellsphillips/flexi/blob/main/LICENSE) © Elliott Phillips
