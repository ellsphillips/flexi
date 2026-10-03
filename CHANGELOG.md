# Changelog

What changed, for the person using Flexi. Versions follow
[semantic versioning](https://semver.org).

## 0.2.0

The terminal application's release, and a rewrite of everything above the
database. Existing SQLite records from development versions are migrated
forward on launch, with a backup taken first.

### The dashboard

- **One key for the clock.** `/` clocks in and out from any screen, and the
  status bar says what was recorded. A session left running past midnight is
  closed at an auto-close time you choose, and Flexi says so with what it
  counted: on launch, on stderr from any command, and on the `/` that finds it,
  which then stops rather than clocking you in. It acts on what the Clock panel
  shows: when the shell or another window has clocked in or out since, the
  press catches the panel up and says so, and the next one acts.
- **The punch strip.** Every day is drawn as cells across the working day,
  filled where you were on the clock, with a tick where the contracted hours are
  met. Seven on one axis is a week's shape at a glance.
- **Records that open.** The records table has the keyboard from the start, its
  cursor on today, so the arrows, `space` and `x` work at once; the cursor is
  lit only while the table has focus. `space` on a day shows the sessions behind
  the figure, the breaks between them, and how the total compares to what the
  day expected.
- **Figures that add up.** A punch keeps its seconds but counts as the minute it
  shows: in at 09:00 and out at 17:00 is 8:00, every column adds up to the total
  under it, and a balance reads the same in the headline, the wallet, Insights
  and the command line.
- **Nothing owed before it is due.** Today's surplus counts the moment it is
  worked, but its contracted hours are not owed until the day ends, so a morning
  opens on last night's balance and a banked day can be booked as TOIL before the
  first punch. Days still to come count nothing, so a month is not a column of
  red −7:24s.
- **A dashboard that keeps up.** While you are on the clock, today's row, the
  period's total and the wallet move with the balance a minute at a time. Left
  open overnight, it closes a forgotten session at the auto-close time, says
  what it counted and moves on to the new day; the next `/` then stops rather
  than clocking you in. A booking or removal on the Leave screen redraws it
  underneath, what a `flexi` command or another window writes shows within
  about two seconds, and `t` reads everything again at once.
- **Work you never clocked.** `n` records a stretch after the fact — a site
  visit, a morning the laptop stayed shut. It counts for everything a punched
  session counts for, and is drawn apart from one. An overlapping stretch is
  refused. `N` lists every correction in the period. The form's examples read
  as examples, an empty field says what to type, and `enter` moves on to an
  empty next field rather than refusing the half-answer.
- **A wrong session can be voided.** `x` on a session in an opened day asks,
  then takes it out of every figure and keeps its clock record. The cursor
  stays in that day, so `n` adds the real hours there: the way back from a
  forgotten clock-out, a late one or a mistyped correction. The question warns
  when a `balance zero` settlement covers the day.
- **A period you move.** `d`, `w`, `m`, `y` and `p` change the span; `[` and `]`
  step it; `t` returns to today; `g` takes a date typed however is quickest —
  `12`, `12 Jun`, `2026-06-12`, `+3d`, `-2w`.

### The leave year

- **A new screen on `f2`**: the whole leave year as one scrolling grid with the
  months stitched together, so a fortnight across the end of July is drawn as a
  fortnight, under weekday initials that stay put as it scrolls. What is booked
  there shows on the dashboard as soon as you go back to it.
- **Booking costs one keystroke.** Put the cursor on a day, press `A`, and it is
  annual leave. `shift` and an arrow extends the selection first.
- **Half days.** `space` cycles a cell between a whole day, a morning and an
  afternoon, and a morning and an afternoon of different types can share a date.
  A half day keeps its `◐` or `◑` at every width, the word beside it giving way
  first. A half day halves what the day expects, whichever side of noon you
  work the rest: in at 11:30 after the dentist, or home sick at one, is
  recorded as it happened. A session forgotten on a half day closes once half
  the day is worked.
- **Five kinds of absence** — annual, sick, TOIL, unpaid and other — where there
  were three.
- **Refusals are per day and reported together.** Book a fortnight over a bank
  holiday and the nine working days go in, with the five it passed over named.

### Insights

`f3` reads the year back in five panels: the running balance day by day, the
balance week by week, annual leave booked against the pace that would spend it
all, the last few weeks side by side, and the whole year on one heatmap. Every
chart stops at today, so a leave year does not draw a cliff of deficits for days
that have not happened yet.

### Balance corrections

- **A balance you already had comes with you.**
  `flexi balance adjust +5:30 --reason "Brought forward"` moves the balance by a
  signed amount, from today or an earlier day of the leave year given with
  `--on`, and shows the balance before and after it asks. `-1:30` reads, and so
  does the `−1:30` Flexi prints. **Adjust balance…** in the command palette does
  the same from any screen, going to the dashboard to ask, and like Record work
  it shows examples and says what an empty field needs.
- `flexi balance zero` draws a line under everything up to a date, writing one
  signed, dated, reasoned row, `settled` unless you give a reason. It settles to
  yesterday by default, because today is not over. With nothing to settle, or a
  later line in the way, it says so before it asks rather than after.
- `flexi balance log` lists every correction, and `flexi balance undo <id>`
  shows the row and asks before removing it, then says what it removed.
- A settlement dated before one already recorded is refused, not
  double-counted, and a correction cannot be dated on or before a settlement.
  Both rules treat an adjustment given an earlier day with `--on` as a
  settlement, since Flexi cannot tell the two apart.

### The command line

`flexi clock in` / `out`, `flexi leave annual mon to fri`, `flexi leave sick
today pm`, `flexi leave cancel next monday`, `flexi balance show`, `flexi
holidays refresh`. `flexi leave` prints its plan and asks before it writes: what
it costs, as in `5 working days, 5 days of annual leave`, the year of any date
outside this one, and how many days fall outside the current leave year.
`--dry-run` stops at the plan, `--yes` skips the question, and a declined
confirmation exits 1 so a script can tell.

Every question takes `enter` as no. Where nothing can answer, as under cron, a
command that would ask says `--yes` is needed in place of a bare `Aborted!`;
`flexi leave` and `balance zero` still read an answer piped to them. A mistyped
option is reported as one, with any real option it is close to, rather than as a
bad date, and `-h` works as `--help` does.

`flexi init` sets a machine up, and run again where records exist it says what is
there and offers to open Flexi, change settings, or start over. Starting over
prints the whole path of the snapshot it takes. Cancelling the first-run
questions says nothing was saved and how to finish, and a command run before
setup points at `flexi`.

Launching the application prints `Opening Flexi…` before the slow imports, so a
first launch is never a blank terminal. Started where there is no terminal to
draw on, the demo says it needs one without pointing at commands a `uvx` visitor
does not have, and on Windows Flexi names the consoles that will do. In a locale
that is not UTF-8, Flexi says to use one rather than failing to draw. The update
notice says to quit Flexi before upgrading; on Windows a running copy makes uv
report the upgrade as failed.

`flexi --demo` opens a leave year of sample records whose balance stays within a
few hours of zero whatever the date. It says the records are samples, and
deletes them when you quit or, on macOS and Linux, close the window.

### Setting up

- **Six questions, once**: when the leave year starts, your entitlement, which
  days you work, how long your working day is, which bank holiday calendar, and
  when to auto-close a forgotten session. Hours take `7:30`, `7h30` or `7.5`.
  The leave-year start takes words, `1 Apr` or `6th April`, and is read back
  beside the field as you type; numbers that read both ways, like `01/04`, are
  refused rather than stored as 4 January. The form fits an 80-column
  terminal, and a refusal is titled with its question and takes the cursor
  back to it.
- **Flexi stamps the day you set it up** and expects nothing of the days before
  it, so installing in November does not open you on seven months of deficit.
  Fill one in with `n` and it counts against your contracted day, as a punched
  day would.
- **Entitlement is per leave year.** `f4` lists the years, edits any of them and
  adds the next. It opens with the cursor in the leave-year start, shown in
  words.
- **Hours a day can change.** `f4` asks first, because every tracked day is
  measured again at the new length, and says when a balance you settled will
  no longer read zero.

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
- **Snapshots, not backups.** One is taken before every migration and the
  newest ten are kept; `flexi init`'s reset takes one that is never aged out.
  They sit beside the database, so keep a copy of your own as well. Restoring is
  a file copy — see the README.
- **A lock file** stops two copies migrating the same database at once.
- **Clock events are immutable**, enforced in the database. A session is never
  edited: voiding it keeps its events, so the audit trail survives.
- **A session under a minute never happened.** Clocking in and straight back out
  is discarded, and the events are kept.

### Look and feel

A warm graphite palette with one cyan accent, hairlines instead of boxes, and
three responsive layouts down to 64 columns, with the dashboard's calendar left
out below 38 rows and the header's version below 100 columns. Colour is never
the only encoding: every coloured cell, rule and bar sits beside a word or a
signed number. A sixteen-colour terminal keeps every rule, track and empty
strip: `TERM=xterm` without `COLORTERM`, as `docker run -it`, PuTTY and many
SSH sessions give, and the Linux console.

`v` puts a one-key badge on every panel, and on the first nine day rows of the
records table. `?` lists every binding on the screen, and the key strip keeps
`/` and `?` when it drops others. `ctrl+p` or `:` opens a command palette
carrying every action, including Quit and those with no key. `q` quits from the
dashboard, Leave and Insights. `:` and `q` are there for VS Code and Cursor,
whose terminals keep `ctrl+p` and `ctrl+q` for the editor; the README has the
setting that hands them back.

### Development and releases

- Documented `just` commands cover development, testing and temporary package
  installation checks. `just check` and `just fix` format the justfile with a
  pinned just, so a newer one on the path cannot fail a clean checkout; the
  GitHub recipes ask for the GitHub CLI when it is missing; and the suite passes
  when run as root, as in a container.
- Each release is installed and checked from TestPyPI before it reaches PyPI,
  and publishing it there needs the owner's approval.

### Known limits

One length of day applies to every working day and every tracked day: hours
that differ by weekday, or change from a given date, cannot be set yet. There is
no export, no import, and no `doctor` command. The flexi balance does not carry
between leave years: it is the sum for the current one, so in April it starts
again from nought. See [`docs/README.md`](docs/README.md) for the rest of the
list.
