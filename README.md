# ⏱️ flexi

A terminal timesheet for people on flexitime. It knows what TOIL is, and that your leave year probably starts in April.

[![version](https://shieldcn.dev/badge/version-0.2.0-00AAAD.svg?variant=outline)](https://pypi.org/project/flexi/)
[![python](https://shieldcn.dev/badge/python-3.12_|_3.13_|_3.14-00AAAD.svg?logo=python&variant=outline)](https://www.python.org)
[![textual](https://shieldcn.dev/badge/tui-textual-00AAAD.svg?logo=textual&variant=outline)](https://textual.textualize.io)
[![sqlite](https://shieldcn.dev/badge/storage-sqlite-00AAAD.svg?logo=sqlite&variant=outline)](https://www.sqlite.org)
[![uv](https://shieldcn.dev/badge/packaging-uv-00AAAD.svg?logo=uv&variant=outline)](https://docs.astral.sh/uv/)

[![ci](https://shieldcn.dev/github/ci/ellsphillips/flexi.svg?variant=outline)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![tests](https://shieldcn.dev/badge/tests-passing-2E9E52.svg?logo=pytest)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![mypy](https://shieldcn.dev/badge/mypy-strict-2E9E52.svg)](https://mypy-lang.org)
[![ruff](https://shieldcn.dev/badge/ruff-select_ALL-2E9E52.svg?logo=ruff)](https://github.com/astral-sh/ruff)
[![licence](https://shieldcn.dev/badge/licence-MIT-2E9E52.svg)](LICENSE)
[![prs](https://shieldcn.dev/badge/PRs-welcome-2E9E52.svg?variant=outline)](CONTRIBUTING.md)

![The dashboard](./docs/shots/showcase-dashboard.svg)

If your employer runs a flexitime scheme you already know the arithmetic. Contracted hours a day, a balance you are allowed to carry, TOIL you can take back as leave. Most people keep it in a spreadsheet and find out in March that they have three days to burn.

Flexi is built for that scheme rather than for time tracking in general. TOIL comes out of the same balance overtime goes into. The leave year starts on the 6th of April, or wherever you put it. It fetches bank holidays from GOV.UK and refuses to book annual leave on one.

Your records live in one SQLite file on your own disk. There is no account and no server.

If you have [uv](https://docs.astral.sh/uv/), you can have a look around right now without installing anything or touching your own data:

```bash
uvx flexi --demo
```

## On the clock

Press `/`. You are on the clock. Press it again and you are off. The status bar says what it recorded and at what time, and that is the whole confirmation: clock events are immutable, so a mistaken `/` costs you one visible break rather than a dialog you would have clicked through anyway.

Each day is drawn as a punch strip. Cells across the working day, filled where you were on the clock, with a tick where your contracted hours are met. Stack a week on one time axis and the shape of how you have been working is obvious. A column of totals never shows you that.

Absence comes in five kinds: annual, sick, TOIL, unpaid, other. Any of them can be a whole day or half of one, and two halves can share a date. People do go home ill at lunchtime.

### Records that open

`space` on a day shows the sessions behind the figure, the breaks between them, and how the total compares to what the day expected.

![Records, expanded](./docs/shots/showcase-records.svg)

### The leave year

![The leave year](./docs/shots/showcase-leave.svg)

`f2` opens the year as one scrolling grid with the months stitched together, so a fortnight across the end of July looks like a fortnight. Put the cursor on a day, press `A`, and it is annual leave. Hold `shift` and arrow first to extend the selection. Book a fortnight over a bank holiday and you get twelve days booked and a note naming the two it skipped.

Colour tells you the type of booking and the glyph tells you whole day or half, so the two never compete for the same cell. Both are spelled out in words beside the grid as well, for anyone the colours do not reach.

![annual](https://shieldcn.dev/badge/●-annual-8451C9.svg)
![sick](https://shieldcn.dev/badge/●-sick-DB703B.svg)
![toil](https://shieldcn.dev/badge/●-TOIL-00AAAD.svg)
![unpaid](https://shieldcn.dev/badge/●-unpaid-BE5BAC.svg)
![bank holiday](https://shieldcn.dev/badge/●-bank_holiday-97B1CD.svg)
![surplus](https://shieldcn.dev/badge/●-surplus-2E9E52.svg)
![deficit](https://shieldcn.dev/badge/●-deficit-CE3E5D.svg)

### Where the balance went

Your balance week by week, annual leave against the pace you need to use it all, the last three weeks side by side, and every day of the year on one heatmap.

![Insights](./docs/shots/showcase-insights.svg)

### Jump mode

`v` puts a letter on every panel and on the first nine rows of the table. Press it to go there. `escape` comes back.

![Jump mode](./docs/shots/showcase-jump.svg)

## Install

Python 3.12, 3.13 or 3.14, on macOS, Linux or Windows. Every release runs the suite on all three platforms, in two timezones, on each interpreter.

<details open>
    <summary><b>Recommended: with uv</b></summary>

[uv](https://docs.astral.sh/uv/) puts Flexi in its own environment and the `flexi` command on your `PATH`. Nothing touches your system Python, and uninstalling takes it all back out.

```bash
# install uv, if you do not have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# restart your shell, or load uv into this one
source $HOME/.local/bin/env

# install flexi, then run it
uv tool install flexi
flexi
```

On Windows, in PowerShell:

```powershell
# install uv, if you do not have it
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# open a new terminal, then
uv tool install flexi
flexi
```

Later:

```bash
uv tool upgrade flexi
uv tool uninstall flexi
```

If your shell cannot find `flexi` afterwards, run `uv tool update-shell` and open a new one.

</details>

<details>
    <summary>With pipx</summary>

```bash
pipx install flexi
```

</details>

<details>
    <summary>With pip</summary>

Only if you know which environment you are installing into: this puts Flexi and its dependencies alongside whatever else is in there.

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

Flexi draws with box-drawing and block characters. [Ghostty](https://ghostty.org), [WezTerm](https://wezterm.org), [Kitty](https://sw.kovidgoyal.net/kitty/) and [Alacritty](https://alacritty.org) render it as shown above. Terminal.app works with flatter colours. On Windows use [Windows Terminal](https://aka.ms/terminal); the old `conhost` console has no truecolour and approximates the strips.

## First run

Five questions, then it gets out of the way.

| | |
|---|---|
| Leave year start | `04-06` for the 6th of April, which is the common one |
| Entitlement | Your annual leave in days, halves allowed |
| Working days | `Mon-Fri`, or `Tue, Thu` if you work part time |
| Bank holiday region | England & Wales, Scotland, or Northern Ireland |
| Auto-close time | When a session you forgot to close should be ended for you |

`flexi --demo` opens a throwaway database holding a plausible working life up to today, and deletes it when you quit. Nothing it does touches your own records.

## Keys

| | |
|---|---|
| `/` | Clock in, or out |
| `f1` `f2` `f3` `f4` | Dashboard, Leave, Insights, Settings |
| `d` `w` `m` `y` | Show a day, a week, a month, or the leave year |
| `[` `]` | Step back and forward |
| `t` · `g` | Today · go to a date |
| `space` | Open a day to its sessions and breaks |
| `A` `S` `T` `U` `O` | Book annual, sick, TOIL, unpaid, other |
| `x` | Remove a booking |
| `v` | Jump mode |
| `?` · `ctrl+p` | Every key · the command palette |

Dates are typed however is quickest: `12`, `12 Jun`, `2026-06-12`, `+3d`, `-2w`.

## From the shell

Not everything needs a full screen.

```bash
flexi clock in                      # and `clock out`
flexi leave annual friday           # book leave in one line
flexi leave annual mon to fri       # or a whole week
flexi leave sick today pm           # or half a day
flexi leave cancel next monday      # and take it back
flexi balance show                  # where you stand
flexi balance zero --reason "..."   # settle a stretch you never tracked
flexi balance log                   # every adjustment, and `undo <id>`
```

`flexi leave` prints the plan and asks before it writes. Weekends and bank holidays are listed rather than quietly dropped, so a fortnight that books twelve of fourteen days tells you which two it left. `--dry-run` stops at the plan, `--yes` skips the question.

`flexi init` sets up a machine. Run it again where records already exist and it tells you what is there, then offers to open Flexi, change settings, or start again.

Starting again is the one thing here that loses data, so it is not a flag. It appears only when there is something to erase, says how many records that is, and writes a snapshot the pruner will never age out before deleting anything. Then it asks you to type a word rather than press a key. With no terminal attached there is no menu at all, which is deliberate: nothing should wipe somebody's records while nobody is watching.

## Your data

One SQLite file, migrated forward on launch with a backup taken first.

```
~/.local/share/flexi/db.db          # your records
~/.local/share/flexi/backups/       # the last ten, taken before each migration
~/.config/flexi/config.yaml         # keybindings and defaults
```

On Windows:

```
%LOCALAPPDATA%\flexi\db.db          # your records
%LOCALAPPDATA%\flexi\backups\       # the last ten, taken before each migration
%APPDATA%\flexi\config.yaml         # keybindings and defaults
```

`XDG_DATA_HOME` and `XDG_CONFIG_HOME` are honoured wherever they are set, Windows included.

Two network calls, both optional and both silent when they fail: bank holidays from GOV.UK, cached for a week, and a version check against PyPI. Your records stay on the machine.

## What it will not do

- Teams. One person, one database, no manager view.
- Invoicing. It measures time against a contract, not against a rate.
- Bank holidays outside the UK. It knows the three GOV.UK divisions and nothing else, so you book those days yourself.
- Automatic tracking. It does not watch your keyboard, your calendar or your repositories.
- Payroll. This is your record, to check theirs against.

## Development

```bash
git clone https://github.com/ellsphillips/flexi
cd flexi
uv sync
uv run pre-commit install

uv run pytest -q                    # the suite, about half a minute
uv run mypy                         # strict, over src and tests
uv run ruff check
uv run python scripts/shoot.py      # regenerate the screenshots above
```

The hooks run what CI runs, from the same locked environment, so a clean commit is a green pipeline.

Three rules hold the codebase together. `flexi.domain` imports neither Textual nor SQLAlchemy, so the arithmetic can be tested without a terminal. The system clock is read in one module. Durations are `timedelta` and never float hours, because 7.4 is not representable in binary floating point and a leave year of rounding it gives you a balance that disagrees with the sum of its own rows. A test walks the imports for the first, a lint rule covers the second, and the third enforces itself.

[`docs/`](docs/) covers the architecture, the domain model, the design system and the keymap. [`CONTRIBUTING.md`](CONTRIBUTING.md) says what a change is expected to come with. [`docs/RELEASING.md`](docs/RELEASING.md) describes the release pipeline.

## Licence

[MIT](LICENSE) © Elliott Phillips
