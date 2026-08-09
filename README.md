# ⏱️ flexi — flexitime, tracked from the terminal

When you were on the clock, how far ahead or behind your contracted hours you are, and what is left in your leave allowances.

[![version](https://shieldcn.dev/badge/version-0.1.0-00AAAD.svg)](https://pypi.org/project/flexi/)
[![python](https://shieldcn.dev/badge/python-3.12_|_3.13-00AAAD.svg?logo=python)](https://www.python.org)
[![built with textual](https://shieldcn.dev/badge/built_with-textual-00AAAD.svg)](https://textual.textualize.io)
[![ci](https://shieldcn.dev/github/ci/ellsphillips/flexi.svg)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)

[![licence](https://shieldcn.dev/badge/licence-MIT-2E9E52.svg)](LICENSE)
[![tests](https://shieldcn.dev/badge/tests-438_passing-2E9E52.svg?logo=pytest)](https://github.com/ellsphillips/flexi/actions/workflows/ci.yaml)
[![mypy](https://shieldcn.dev/badge/mypy-strict-2E9E52.svg)](https://mypy-lang.org)
[![ruff](https://shieldcn.dev/badge/ruff-select_ALL-2E9E52.svg?logo=ruff)](https://github.com/astral-sh/ruff)
[![prs](https://shieldcn.dev/badge/PRs-welcome-2E9E52.svg?variant=outline)](CONTRIBUTING.md)

![The dashboard](./docs/shots/showcase-dashboard.svg)

Flexitime only works if you can see it. A spreadsheet gives you a number; it does not tell you that you have quietly banked forty minutes a day for three weeks, or that your annual leave is running behind the pace you would need to use it all. Flexi draws the month as punch strips on a shared time axis, so the shape of how you have been working is the first thing you see.

> **Why a flexitime tracker in the terminal?**
>
> Because clocking in should cost one keystroke from wherever you already are, and the answer to "can I go home yet" should be on screen before your hands leave the home row. `/` clocks in or out from any screen. Nothing to confirm — clock events are immutable and a second `/` opens a new session, so a mistaken press costs one visible break, and the status bar says exactly what was recorded.

## ✨ Features

- **One key to clock in or out**, from any screen, with the elapsed time ticking in the border
- **The day as a punch strip** — a row of cells across the working day, filled where you were on the clock, with a tick where your contracted hours will have been met
- **Every kind of absence** — annual, sick, TOIL, unpaid and other, in whole days or mornings and afternoons; a sick morning and an annual afternoon can share a date, because that happens
- **A whole leave year on one scrolling grid**, months stitched together so a fortnight spanning July reads as a fortnight
- **Allowances against a pace marker**, so "18.5 days left" becomes "18.5 left, and behind where an even spread would put you"
- **Bank holidays** fetched for your division and cached, so booking leave over one is refused rather than silently wasted
- **Jump mode** — one key puts a badge on every panel and on the first nine day rows
- **Migrations with a backup taken first**, and the last ten kept
- **Responsive** to 64 columns; the layout sheds panels rather than truncating them

### Records that open

`space` on any day shows the sessions behind the figures, the breaks between them, what the day expected and what it got.

![Records, expanded](./docs/shots/showcase-records.svg)

### A leave year you book directly on

`A` books annual leave on the cursor, `shift`+arrows extend to a range, `space` cycles to half-days, `x` removes. A fortnight across a bank holiday books twelve days and says so, rather than refusing all fourteen.

![The leave year](./docs/shots/showcase-leave.svg)

Colour carries the *type* of a booking and the glyph carries its *portion*, so the two never compete for the same cell — and every colour is spelled out in words beside it, so nothing here is colour alone:

![annual](https://shieldcn.dev/badge/●-annual-8451C9.svg)
![sick](https://shieldcn.dev/badge/●-sick-DB703B.svg)
![toil](https://shieldcn.dev/badge/●-TOIL-00AAAD.svg)
![unpaid](https://shieldcn.dev/badge/●-unpaid-BE5BAC.svg)
![bank holiday](https://shieldcn.dev/badge/●-bank_holiday-97B1CD.svg)
![surplus](https://shieldcn.dev/badge/●-surplus-2E9E52.svg)
![deficit](https://shieldcn.dev/badge/●-deficit-CE3E5D.svg)

### Where the balance actually went

The balance week by week, annual leave against its pace line, the shape of the last three weeks, and every day of the year on a diverging heatmap.

![Insights](./docs/shots/showcase-insights.svg)

### Jump mode

`v`, then the badge. `escape` puts you back exactly where you were.

![Jump mode](./docs/shots/showcase-jump.svg)

## 📦 Installation

Requires Python 3.12 or later, on macOS or Linux.

<details open>
    <summary><b>Recommended: with uv</b></summary>

[uv](https://docs.astral.sh/uv/) is a fast Python package manager. `uv tool install` puts Flexi in its own isolated environment and `flexi` on your `PATH`, so nothing lands in your system Python.

#### macOS / Linux

```bash
# install uv (skip if you already have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# restart your shell, or load it into this one
source $HOME/.local/bin/env

# install flexi, and run it
uv tool install flexi
flexi
```

Upgrading, and removing it entirely:

```bash
uv tool upgrade flexi
uv tool uninstall flexi
```

If `flexi` is not found after installing, uv's tool directory is not on your `PATH` yet:

```bash
uv tool update-shell
```

</details>

<details>
    <summary>With pipx</summary>

```bash
pipx install flexi
```

</details>

<details>
    <summary>With pip</summary>

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

> **Tip:** Flexi draws with box-drawing and block characters. Any modern terminal handles them — [Ghostty](https://ghostty.org), [WezTerm](https://wezterm.org), [Kitty](https://sw.kovidgoyal.net/kitty/) and [Alacritty](https://alacritty.org) all render it as shown above. Terminal.app will do, but its colour handling is dated.

## 🚀 Usage

First launch asks four questions — when your leave year starts, which days you work, which bank holidays apply, and when an open session should auto-close. Everything after that is keyboard.

| | |
|---|---|
| `/` | Clock in, or out |
| `f1` `f2` `f3` `f4` | Dashboard, Leave, Insights, Settings |
| `d` `w` `m` `y` | Day, week, month or leave year |
| `[` `]` · `t` · `g` | Step back and forward · today · go to a date |
| `space` | Open a day to its sessions and breaks |
| `A` `S` `T` `U` `O` · `x` | Book annual, sick, TOIL, unpaid, other · remove |
| `v` | Jump mode |
| `?` · `ctrl+p` | The whole keyboard · the command palette |

Dates are typed however is quickest — `12`, `12 Jun`, `2026-06-12`, `+3d`, `-2w`.

There is a small CLI for the things a full screen is the wrong shape for:

```bash
flexi clock in                      # and `clock out`
flexi balance show                  # where you stand
flexi balance zero --reason "..."   # settle a stretch that was never tracked
flexi balance log                   # every adjustment, and `undo <id>`
flexi --demo                        # a throwaway database, deleted on exit
```

## 💾 Where your data lives

One SQLite database under the XDG base directories, migrated forward on launch with a backup taken first:

```
~/.local/share/flexi/db.db          # your records
~/.local/share/flexi/backups/       # the last ten, taken before each migration
~/.config/flexi/config.yaml         # keybindings and defaults
```

Nothing is uploaded. Flexi makes two network calls — a bank-holiday fetch from GOV.UK and an update check against PyPI — and both fail silently.

## 🛠️ Development

```bash
git clone https://github.com/ellsphillips/flexi
cd flexi
uv sync                             # everything, including dev dependencies
uv run pre-commit install           # the hooks are the commands CI runs

uv run pytest -q                    # 438 tests, about a minute
uv run mypy                         # strict, over src and tests both
uv run ruff check
uv run python scripts/shoot.py      # regenerate the screenshots above
```

`ruff` runs with `select = ALL` and a short ignore list, each entry carrying its reason. Every wide signature is keyword-only, every module reads the system clock through `flexi.wallclock`, and `flexi.domain` imports neither Textual nor SQLAlchemy — a test enforces the last of those.

Architecture, the domain model, the design system and the keymap are in [`docs/`](docs/). [`CONTRIBUTING.md`](CONTRIBUTING.md) covers what a change is expected to come with.

## 📄 Licence

[MIT](LICENSE) © Elliott Phillips
