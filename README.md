# ⏱️ flexi

A terminal app for people on flexitime. It records when you were on the clock, works out how far ahead or behind your contracted hours you are, and keeps track of what is left in your leave.

[![version](https://shieldcn.dev/badge/version-0.1.0-00AAAD.svg?variant=outline)](https://pypi.org/project/flexi/)
[![python](https://shieldcn.dev/badge/python-3.12_|_3.13-00AAAD.svg?logo=python&variant=outline)](https://www.python.org)
[![textual](https://shieldcn.dev/badge/tui-textual-00AAAD.svg?logo=textual&variant=outline)](https://textual.textualize.io)
[![sqlite](https://shieldcn.dev/badge/storage-sqlite-00AAAD.svg?logo=sqlite&variant=outline)](https://www.sqlite.org)
[![uv](https://shieldcn.dev/badge/packaging-uv-00AAAD.svg?logo=uv&variant=outline)](https://docs.astral.sh/uv/)

[![ci](https://shieldcn.dev/github/ci/ellsphillips/flexi.svg?variant=outline)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![tests](https://shieldcn.dev/badge/tests-489_passing-2E9E52.svg?logo=pytest)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![mypy](https://shieldcn.dev/badge/mypy-strict-2E9E52.svg)](https://mypy-lang.org)
[![ruff](https://shieldcn.dev/badge/ruff-select_ALL-2E9E52.svg?logo=ruff)](https://github.com/astral-sh/ruff)
[![licence](https://shieldcn.dev/badge/licence-MIT-2E9E52.svg)](LICENSE)
[![prs](https://shieldcn.dev/badge/PRs-welcome-2E9E52.svg?variant=outline)](CONTRIBUTING.md)

![The dashboard](./docs/shots/showcase-dashboard.svg)

## Who this is for

If your employer runs a flexitime scheme, you already know the arithmetic. Contracted hours per day, a running balance you are allowed to carry, TOIL you can take back as leave, and a leave year that probably starts in April rather than January. Most people track this in a spreadsheet, badly, and find out in March that they have three days to burn.

Flexi is built around that specific scheme. It understands TOIL as a withdrawal from the same balance your overtime accrues into. It knows the leave year does not start on the 1st of January. It fetches bank holidays from GOV.UK for England & Wales, Scotland or Northern Ireland, and refuses to let you book annual leave on one.

It is a single-user, local application. There is no account, no server, and nothing leaves your machine.

## What it does

Press `/` and you are on the clock. Press it again and you are off. The status bar tells you what it recorded and at what time, which is the only confirmation you get, because clock events are immutable and pressing `/` by mistake costs you one visible break rather than a dialog.

Each day is drawn as a punch strip: a row of cells across the working day, filled where you were on the clock, with a tick marking where your contracted hours will have been met. Stack a week of them on a shared time axis and you can see the shape of how you have been working, which no column of totals will show you.

Leave is booked on a calendar, not in a form. `f2` opens the whole leave year as one scrolling grid with the months stitched together, so a fortnight spanning the end of July looks like a fortnight. Put the cursor on a day, press `A`, and it is annual leave. Hold `shift` and press the arrows to extend the selection first. Book a fortnight that crosses a bank holiday and it books the twelve working days and tells you it skipped two.

Absence comes in five kinds: annual, sick, TOIL, unpaid and other. Any of them can be a whole day, a morning or an afternoon, and two different halves can share a date, because going home ill at lunchtime is a thing that happens.

### Records that open

Press `space` on a day to see the sessions behind the figures, the breaks between them, and how the total compares to what the day expected.

![Records, expanded](./docs/shots/showcase-records.svg)

### The leave year

![The leave year](./docs/shots/showcase-leave.svg)

Colour carries the type of a booking, and the glyph carries whether it is a whole day or half of one, so the two never compete for the same cell. Every colour is also written out in words next to it.

![annual](https://shieldcn.dev/badge/●-annual-8451C9.svg)
![sick](https://shieldcn.dev/badge/●-sick-DB703B.svg)
![toil](https://shieldcn.dev/badge/●-TOIL-00AAAD.svg)
![unpaid](https://shieldcn.dev/badge/●-unpaid-BE5BAC.svg)
![bank holiday](https://shieldcn.dev/badge/●-bank_holiday-97B1CD.svg)
![surplus](https://shieldcn.dev/badge/●-surplus-2E9E52.svg)
![deficit](https://shieldcn.dev/badge/●-deficit-CE3E5D.svg)

### Where the balance went

Your balance week by week, annual leave against the pace you would need to use it all, the last three weeks side by side, and every day of the year on one heatmap.

![Insights](./docs/shots/showcase-insights.svg)

### Jump mode

`v` puts a single-character badge on every panel and on the first nine rows of the table. Press the badge to go there. `escape` puts you back.

![Jump mode](./docs/shots/showcase-jump.svg)

## Installation

Python 3.12 or later. Tested on macOS and Linux.

<details open>
    <summary><b>Recommended: with uv</b></summary>

[uv](https://docs.astral.sh/uv/) installs Flexi into its own isolated environment and puts the `flexi` command on your `PATH`, so nothing lands in your system Python and uninstalling leaves nothing behind.

```bash
# install uv, if you do not have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# restart your shell, or load uv into this one
source $HOME/.local/bin/env

# install flexi, then run it
uv tool install flexi
flexi
```

Later:

```bash
uv tool upgrade flexi
uv tool uninstall flexi
```

If your shell cannot find `flexi` after installing, uv's tool directory is not on your `PATH`. Run `uv tool update-shell` and open a new shell.

</details>

<details>
    <summary>With pipx</summary>

```bash
pipx install flexi
```

</details>

<details>
    <summary>With pip</summary>

Only if you know the environment you are installing into. `pip install flexi` puts Flexi and its dependencies alongside everything else in that environment.

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

Flexi draws with box-drawing and block characters. [Ghostty](https://ghostty.org), [WezTerm](https://wezterm.org), [Kitty](https://sw.kovidgoyal.net/kitty/) and [Alacritty](https://alacritty.org) all render it as shown above. macOS Terminal.app works, but its colours are flatter than the screenshots.

## First run

Flexi asks five questions and then gets out of the way:

| | |
|---|---|
| Leave year start | `04-06` for the 6th of April, which is the common one |
| Entitlement | Your annual leave in days, halves allowed |
| Working days | `Mon-Fri`, or `Tue, Thu` if you work part time |
| Bank holiday region | England & Wales, Scotland, or Northern Ireland |
| Auto-close time | When a session you forgot to close should be ended for you |

To look around before committing your own data, `flexi --demo` runs against a throwaway database seeded with six weeks of a plausible working life. It is deleted when you quit.

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

There is a small CLI for the things that do not need a full screen:

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

`flexi leave` prints the plan and asks before it writes anything. Weekends and
bank holidays are listed rather than silently dropped, so a fortnight that books
twelve of fourteen days tells you which two it left and why. `--dry-run` stops
after the plan; `--yes` skips the question for a script.

`flexi init` sets Flexi up, and `flexi init --reset` erases everything and starts
again — the one command here that loses data. It takes a verified snapshot
first, tells you what it is about to remove, and asks you to type the word.

## Your data

One SQLite file, migrated forward on launch with a backup taken first:

```
~/.local/share/flexi/db.db          # your records
~/.local/share/flexi/backups/       # the last ten, taken before each migration
~/.config/flexi/config.yaml         # keybindings and defaults
```

`XDG_DATA_HOME` and `XDG_CONFIG_HOME` are honoured if you set them. On Windows, Flexi uses `%LOCALAPPDATA%` and `%APPDATA%` instead.

Two network calls are made, both optional and both silent on failure: bank holidays from GOV.UK, cached for a week, and a version check against PyPI. Your records never leave the machine.

## What it does not do

- **Teams.** One person, one database. There is no sharing, no approval workflow and no manager view.
- **Invoicing or client billing.** It measures time against a contract, not against a rate.
- **Non-UK bank holidays.** The three GOV.UK divisions are the only ones it knows. You can still use everything else, but you will have to book public holidays as leave yourself.
- **Automatic tracking.** It does not watch your keyboard, your calendar or your repositories. You tell it when you started.
- **Payroll.** Nothing here is authoritative for your employer. It is your own record, to check theirs against.

## Development

```bash
git clone https://github.com/ellsphillips/flexi
cd flexi
uv sync
uv run pre-commit install

uv run pytest -q                    # 489 tests, about a minute
uv run mypy                         # strict, over src and tests
uv run ruff check
uv run python scripts/shoot.py      # regenerate the screenshots above
```

The pre-commit hooks run the same commands CI does, through the same locked environment, so a clean commit is a green pipeline.

Three rules hold the codebase together: `flexi.domain` imports neither Textual nor SQLAlchemy and is tested without a terminal, the system clock is read in exactly one module, and durations are `timedelta` rather than float hours. The first is enforced by a test that walks the imports; the second by a lint rule; the third by arithmetic that would not otherwise add up.

[`docs/`](docs/) covers the architecture, the domain model, the design system and the keymap. [`CONTRIBUTING.md`](CONTRIBUTING.md) says what a change is expected to come with, and [`docs/RELEASING.md`](docs/RELEASING.md) describes the release pipeline.

## Licence

[MIT](LICENSE) © Elliott Phillips
