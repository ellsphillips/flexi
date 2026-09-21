# Changelog

What changed, for the person using Flexi. Versions follow
[semantic versioning](https://semver.org).

## 0.2.1

- Document the `just` commands for development, testing, package checks, and
  trying a release locally before approving publication.
- Check both package builds and minimum dependency versions in temporary
  environments, keeping the development lockfile and environment intact.

## 0.2.0

The terminal application's release, and a rewrite of everything above the
database. Existing SQLite records from development versions are migrated
forward on launch, with a backup taken first.

### The dashboard

- **One key for the clock.** `/` clocks in and out from any screen, and the
  status bar says what was recorded. A session left running past midnight is
  closed at an auto-close time you choose, and the row says so.
- **The punch strip.** Every day is drawn as cells across the working day,
  filled where you were on the clock, with a tick where the contracted hours are
  met. Seven on one axis is a week's shape at a glance.
- **Records that open.** `space` on a day shows the sessions behind the figure,
  the breaks between them, and how the total compares to what the day expected.
- **Work you never clocked.** `n` records a stretch after the fact — a site
  visit, a morning the laptop stayed shut. It counts for everything a punched
  session counts for, and is drawn apart from one. An overlapping stretch is
  refused. `N` lists every correction in the period.
- **A period you move.** `d`, `w`, `m`, `y` and `p` change the span; `[` and `]`
  step it; `t` returns to today; `g` takes a date typed however is quickest —
  `12`, `12 Jun`, `2026-06-12`, `+3d`, `-2w`.

### The leave year

- **A new screen on `f2`**: the whole leave year as one scrolling grid with the
  months stitched together, so a fortnight across the end of July is drawn as a
  fortnight.
- **Booking costs one keystroke.** Put the cursor on a day, press `A`, and it is
  annual leave. `shift` and an arrow extends the selection first.
- **Half days.** `space` cycles a cell between a whole day, a morning and an
  afternoon, and a morning and an afternoon of different types can share a date.
- **Five kinds of absence** — annual, sick, TOIL, unpaid and other — where there
  were three.
- **Refusals are per day and reported together.** Book a fortnight over a bank
  holiday and the nine working days go in, with the five it passed over named.

### Insights

`f3` reads the year back in five panels: the running balance day by day, the
balance week by week, annual leave against the pace that would spend it all, the
last few weeks side by side, and the whole year on one heatmap. Every chart stops
at today, so a leave year does not draw a cliff of deficits for days that have
not happened yet.

### Balance corrections

- `flexi balance zero` draws a line under everything up to a date, writing one
  signed, dated, reasoned row. It settles to yesterday by default, because today
  is not over.
- `flexi balance log` lists every correction, and `flexi balance undo <id>`
  removes one.
- A settlement that an earlier one already covers is refused, not
  double-counted.

### The command line

`flexi clock in` / `out`, `flexi leave annual mon to fri`, `flexi leave sick
today pm`, `flexi leave cancel next monday`, `flexi balance show`, `flexi
holidays refresh`. `flexi leave` prints its plan and asks before it writes;
`--dry-run` stops at the plan, `--yes` skips the question, and a declined
confirmation exits 1 so a script can tell.

`flexi init` sets a machine up, and run again where records exist it says what is
there and offers to open Flexi, change settings, or start over.

### Setting up

- **Five questions, once**: when the leave year starts, your entitlement, which
  days you work, which bank holiday calendar, and when to auto-close a forgotten
  session.
- **Flexi stamps the day you set it up** and expects nothing of the days before
  it, so installing in November does not open you on seven months of deficit.
- **Entitlement is per leave year.** `f4` lists the years, edits any of them and
  adds the next.

### Bank holidays

Fetched from GOV.UK for England & Wales, Scotland or Northern Ireland and cached
for a week. Until Flexi has a calendar it refuses to book absence, and says so,
because a day it cannot rule out as a bank holiday is a day it cannot count. A
fetch that fails is remembered, so a machine with no network does not spend its
whole budget in front of every command.

### Your data

- One SQLite file under `~/.local/share/flexi` (`%LOCALAPPDATA%\flexi` on
  Windows), with `XDG_DATA_HOME` and `XDG_CONFIG_HOME` honoured when they hold
  an absolute path.
- **Backups.** A snapshot is taken before every migration and the newest ten are
  kept; `flexi init`'s reset takes one that is never aged out. Restoring is a
  file copy — see the README.
- **A lock file** stops two copies migrating the same database at once.
- **Clock events are immutable**, enforced in the database. Correcting a session
  writes a replacement and voids the original, so the audit trail survives.
- **A session under a minute never happened.** Clocking in and straight back out
  is discarded, and the events are kept.

### Look and feel

A warm graphite palette with one cyan accent, hairlines instead of boxes, and
three responsive layouts down to 64 columns. Colour is never the only encoding:
every coloured cell, rule and bar sits beside a word or a signed number.

`v` puts a one-key badge on every panel, and on the first nine day rows of the
records table. `?` lists every binding on the screen. `ctrl+p` opens a command
palette carrying every action, including those with no key.

### Known limits

A contract other than 37 hours cannot be set yet; there is no export, no import,
and no `doctor` command. The flexi balance does not carry between leave years:
it is the sum for the current one, so in April it starts again from nought. See
[`docs/README.md`](docs/README.md) for the rest of the list.
