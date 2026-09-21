# Testing

Four kinds of test, each answering a different question. A change is not done
until the kinds it touches are green.

| Kind | Question | Where | Speed |
|---|---|---|---|
| **Domain** | Is the arithmetic right? | `tests/domain/` | instant |
| **Service** | Does the write happen, and is it refused when it should be? | `tests/services/` | fast, a SQLite file under `tmp_path` |
| **Pilot** | Does the keypress do the thing? | `tests/tui/` | ~50 ms each |
| **Snapshot** | Does it still look right? | `tests/snapshot/` | ~100 ms each |

---

## 1. Domain tests

`flexi/domain/` has no I/O, so these are table-driven and there are a lot of them.
The three that matter most:

```python
@pytest.mark.parametrize(
    ("value", "want"),
    [
        (timedelta(0), "0:00"),
        (timedelta(minutes=48), "+0:48"),
        (timedelta(hours=-4, minutes=-14), "−4:14"),  # U+2212, not a hyphen
    ],
)
def test_delta_formatting(value, want):
    assert delta(value) == want
```

`flexi.domain.format` also carries doctests, and `pytest` runs them: `pyproject`
puts `src/flexi/domain`, `src/flexi/cli/ui` and `src/flexi/services` on
`testpaths` with `--doctest-modules`. An example in a docstring there is a test.

`Period` gets a full matrix: every granularity × {start, end, label, shift(±1),
zoom, contains}, including the boundaries that break naive implementations —
31 January `shift(+1)` on `MONTH`, a `WEEK` spanning a year end, and a `YEAR`
whose leave year starts on `04-06`.

`punch.buckets()` is tested against exact expected strings, because the strip is
the signature and a one-cell drift is invisible in review and obvious in use.

## 2. Service tests

The fixtures are already written; use them.
`tests/conftest.py` gives every test an `engine` and a `session` against a
throwaway SQLite file under `tmp_path`. `tests/services/conftest.py` adds
`configure` — one call that sets Flexi up and hands back a built `Services`
registry — plus `services` for the common case and `work()` for putting a day of
hours on the clock.

`tests.database.create_schema()` creates the tables, indexes, and triggers in
one explicit transaction. The Python SQLite driver's
[legacy transaction mode](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#legacy-transaction-mode-with-the-sqlite3-driver)
otherwise commits each schema statement separately, adding disk writes to every
new test database. Tests still use isolated files and the application's normal
connection settings.

```python
def test_clock_in_is_refused_on_a_day_booked_off(configure):
    services = configure(entitlement=(2026, 25.0))
    services.absence.book(date(2026, 6, 10), AbsenceType.ANNUAL, Portion.FULL)

    result = services.clock.clock_in(now=datetime(2026, 6, 10, 9, tzinfo=UTC))

    assert not result.success
```

**`configure` seeds a bank holiday, and it has to.**
`BankHolidayService.titles_between` answers `None` — not an empty mapping — when
the calendar is absent, and `AbsenceService` refuses to book against `None`. A
test that arranges its own database without a cache row does not fail loudly: it
gets "Bank holiday data unavailable" back from every booking and then asserts
something else, which is how a test passes while exercising nothing.

**Anything that touches "now" must pin the clock.** The suite pins the *zone*
session-wide through `flexi.wallclock`, but not the date, so a test that reads
the real one is a test that fails on some future Tuesday for reasons that have
nothing to do with the code. Two files learned this the hard way — see
`tests/services/test_short_sessions.py` and `test_stale_sessions.py`, both of
which hold the clock still with `time_machine.travel(..., tick=False)` in a
module-level autouse fixture, and say why in the docstring. `configure`'s seeded
holiday is the usual trap: on the day after it, every `clock_in()` is refused.

Every service method that returns a `Result` needs both branches tested. The
refusal message is part of the contract — it is what the status bar shows.

**Migrations.** `tests/models/test_migrations.py` upgrades and downgrades a
*populated* database. `0007` rebuilds `absence_days` instead of altering it, and
a table rebuild that silently loses rows is the kind of bug only discovered by the
person whose leave records it ate.

Write to those tables with **raw SQL, not the ORM**: the models carry columns a
later revision adds, so writing through them tests the schema against itself
instead of against what is on disk.

## 3. Pilot tests

Textual's `App.run_test()` drives the real application.

```python
async def test_slash_toggles_the_clock(app_factory):
    async with app_factory().run_test() as pilot:
        await pilot.press("/")
        assert pilot.app.services.clock.is_clocked_in()
        assert "Clocked in" in status_text(pilot.app)
        await pilot.press("/")
        assert not pilot.app.services.clock.is_clocked_in()
```

`app_factory` builds a `FlexiApp` against a temporary database with settings
already saved, so the setup screen does not intercept. It lives in
`tests/tui/conftest.py`.

What to cover here, at minimum:

- `/` clocks in and out from every screen, and does **not** fire inside an
  `Input`.
- `space` expands the row under the cursor and the cursor does not move.
- `d`/`w`/`m`/`y` give the records table a row per day in the period plus one
  total row: 2, 8, 29–32, 366.
- `t` returns to today from any period.
- `v` opens the overlay, a target key focuses that panel, `escape` restores the
  previously focused widget.
- Every modal binds `escape` and `enter` — `tests/tui/test_keyboard.py` walks
  `flexi.screens` and finds them, so a modal written next week is covered the day
  it is written.
- No two *shown* bindings on a screen share a key, and every binding names an
  action that exists (same file). A typo in an action name is otherwise silent
  until a user presses the key and finds it does nothing.
- The layering test (`tests/test_layering.py`): `domain/` imports neither
  `textual` nor `sqlalchemy`; `components/` and `screens/` do not import
  `sqlalchemy`.

## 4. Snapshot tests

`tests/snapshot/test_screens.py` drives every case in its `CASES` tuple and
compares what the compositor produced against the text committed in
`docs/shots/`.

**Text, not SVG.** `pytest-textual-snapshot` compares rendered SVGs, which are
only readable as pictures — a CI failure becomes a file you have to download
before you can tell whether the change was intended. So Flexi does not use it.
Comparing characters means a failure prints a unified diff of two screens, in the
terminal, where whoever caused it is already looking:

```
-  │ ANNUAL LEAVE  20.5 left of 25 │
+  │ ANNUAL LEAVE  19.5 left of 25 │
```

The SVGs are still written and are still what a reviewer looks at; they are just
not what the test asserts on.

Rules that keep them useful:

- **Freeze time.** Every case runs inside `time_machine.travel` at
  `flexi.services.samples.NOW` against the seeded database, otherwise the diff is
  the clock.
- **Pin the widths the case is about.** The three are 120×36 (wide), 84×28
  (narrow) and 64×22 (tiny). The dashboard is pinned at all three, leave at wide
  and narrow, insights at 120×36 and 120×44, and the rest at wide only. The
  responsive rules in `DESIGN-SYSTEM.md` §6 only exist where they are pinned.
- **Regenerate, then read the diff.** `just shots` rewrites
  both the SVGs and the text; the diff is what you review before committing.
- **A version bump is a visual change.** The header carries `v0.2.0`, so every
  `.txt` twin carries it too, and bumping `version` in `pyproject.toml` without
  re-shooting turns the snapshot suite red.

## 5. Screenshots for review

Snapshots are for regression. For "show me what it looks like", drive the real
app headlessly and export SVG:

```python
# scripts/shoot.py
def build_database(path: Path) -> Session: ...


async def shoot(name: str, size: tuple[int, int], keys: list[str], db: Path) -> None:
    ...
    app.save_screenshot(str(SHOTS / f"{name}.svg"))
    (SHOTS / f"{name}.txt").write_text(screen_text(app), encoding="utf-8")
```

`just shots` writes the set into `docs/shots/`, as an SVG and
a text twin per screen. `SHOOTS` there is `CASES` plus the five wider
`showcase-*` shots the README embeds; the two lists are kept in step by hand.
Convert to PNG for a terminal that renders images:

```
rsvg-convert -w 1600 docs/shots/dashboard-wide.svg -o /tmp/dashboard-wide.png
```

The demo seeds a leave year of plausible data — some overtime, one short day, a
week of annual leave, the year's bank holidays, a sick day, a half day and a TOIL
day — so the shots show the interesting cases and not an empty database. It lives
in `flexi/services/samples.py` and is what `flexi --demo` runs, which makes it
the same data a reviewer, a snapshot test and a new user all see.

Everything is derived from the anchor it is handed. The snapshots pass `ANCHOR`,
a fixed Thursday, because a committed SVG cannot move; `--demo` passes today,
along with the wall time that day has reached, so nothing is seeded that has not
happened yet. `--demo` builds it in a temporary directory and throws it away on
exit.

## 6. Running

Follow [Developer tasks](TASKS.md) for the tool requirements and `just setup`.
Run `just` to list the recipes; test recipes accept pytest paths and options.

```bash
just test                           # everything
just test tests/domain -q            # while working on arithmetic
just test tests/tui -q               # while working on interaction
just shots                          # after an intentional visual change
just check                          # lockfile, style, types, and workflows
just coverage                       # CI property budget and coverage reports
```

`just types` checks `src` and `tests` with mypy's native and Windows platform
views. `just lint` checks Python and justfile style without applying fixes.

Use `just fix` to apply automated style fixes, or `just hooks` to run every
configured pre-commit hook. Both may modify files; review the diff afterward.

A failed snapshot prints its diff, so nothing needs uploading as an artefact.

`pytest-timeout` bounds each test at 120 seconds and prints thread stacks when
that limit is reached. The stateful service test has a 300-second limit because
one test item exercises many database lifecycles. Keep timeout diagnostics under
that same timer: a separate `faulthandler_timeout` ignores per-test limits. A
Windows worker crashed during that extra stack dump before its test limit.
Python's fatal-error handler remains enabled for actual interpreter faults.

## 7. Reproducing a loaded runner

```bash
just test-late -q
```

This uses the CI property-test profile and sets `FLEXI_LATE_CALLBACKS=0.05`.

`pilot.pause()` drains the messages queued at the moment it is called. Work that
a *layout* schedules — `RecordsModule` measuring its strip column, the key strip
recomposing — may or may not have landed by the time it returns, and which of
those happens is a property of how loaded the machine is, not of the
code. On a laptop it lands early and every test passes. On a three-core runner
it lands a moment later, on top of whatever the test had just set up: a table the
test emptied fills again, a ledger cache the test just invalidated refills.

That variable puts every deferred callback behind a timer, which is the one thing
`pause` cannot drain, so a loaded runner's ordering is reproducible on an idle
machine in twenty seconds. **It is expected to be green**, and a test that passes
without it and fails with it has not found a bug — it is asserting on a screen
that had not finished drawing. The cure is `await settled(pilot)` from
`tests/conftest.py`, which waits for the callbacks themselves instead of
guessing at a number of pauses.

Both failures that motivated it were real CI failures, in different files, that
reproduced locally in under a second once the ordering was made deterministic.

## 8. Running what CI runs, without pushing

CI is three reusable workflows, called by both `ci.yaml` and `release.yaml` so
that a release is verified by exactly what a pull request is verified by. Every
job in them is a command you can run here. Nothing in CI is discoverable only
by pushing.

| Workflow | Job | The same thing, locally |
|---|---|---|
| `static.yaml` | `Lint and types` | `just check` and `just audit` |
| `tests.yaml` | the matrix | `just test-ci` on the row's interpreter and timezone |
| `tests.yaml` | the coverage row | `just coverage` |
| `tests.yaml` | `The declared floors still pass` | `just test-floors` |
| `tests.yaml` | `Deferred callbacks land late` | `just test-late` |
| `package.yaml` | `Wheel installs and runs` | `just package-check` |

`tests/test_pipelines.py` asserts that both pipelines call the same three, and
that `All green` waits for all of them.

`just test-floors` copies the current checkout, including uncommitted changes,
and resolves the lowest direct dependencies in a temporary lockfile and virtual
environment. It does not rewrite your working lockfile or replace your `.venv`.
Locally it uses the oldest supported Python, 3.12, avoiding source builds of
older dependencies on newer interpreters; uv selects or downloads that
interpreter without switching your development environment. CI additionally
checks floors on the repository's Python version from `.python-version`.
Pass pytest arguments to narrow a reproduction, such as
`just test-floors tests/services -q`.

`just package-check` builds both `flexi` and `flexi-test`, checks metadata and
README rendering with Twine, and installs each wheel separately. Runtime smoke
checks run before the locked test tools are added for the installed-package
tests. Builds, environments, configuration, and data are temporary on every OS.
Neither command publishes anything. CI retains the direct commands in its
workflows and separately tests the recipes on all three operating systems.

The full suite runs in 15 matrix jobs. Every Linux, macOS and Windows runner
tests Python 3.12, 3.13 and 3.14 in UTC. Each OS also tests Python 3.13 in
Europe/London and America/New_York. This retains every OS/Python pair and every
OS/timezone pair without repeating all 27 combinations.

The canonical Linux/Python 3.13/UTC job enforces coverage. Every matrix job,
the minimum-dependency job and the delayed-callback job use Hypothesis's `ci`
profile: up to 500 examples per property. The stateful SQLite test keeps its
separate budget of 40 examples with up to 40 steps each on every runner. No
tests are excluded from any of these jobs. Each run prints its 15 slowest tests
with `--durations=15`, so further performance work can target measured bottlenecks.

`uv` selects the interpreter; `TZ` selects the timezone on POSIX systems:

```
UV_PROJECT_ENVIRONMENT=/tmp/py313 uv sync --locked --dev --python 3.13
HYPOTHESIS_PROFILE=ci TZ=Europe/London /tmp/py313/bin/python -m pytest -q
HYPOTHESIS_PROFILE=ci TZ=America/New_York /tmp/py313/bin/python -m pytest -q
```

An operating system needs its own runner; running these commands on macOS does
not verify Windows or Linux. The suite pins its
own clock through `flexi.wallclock`, so `TZ` is not what makes the timezone
rows differ — the machine underneath them is, and green under all three is the
evidence that no reading escapes the pin. Windows sets its zone with `tzutil`
and not with `TZ`, which is a POSIX idea `time.tzset` implements and Windows
does not have. Two tests are skipped there and say so: the pty reader in
`tests/cli/test_terminal.py`, which needs a terminal Windows has no equivalent
of, and the pair in `tests/services/test_setup.py` that need a file `chmod`
can genuinely deny.

The workflow files themselves are checked by the linter that knows about them:

```bash
just workflow-check
```

`act` can run Linux jobs in Docker. Its images may differ from GitHub's hosted
runners, and it does not reproduce the macOS or Windows matrix rows:

```
act workflow_dispatch -W .github/workflows/ci.yaml
```

## 9. Dependency advisories

The static workflow checks every locked runtime and development dependency:

```bash
just audit
```

The recipe exports requirements to temporary storage and uses a pinned
pip-audit version.

The audit evaluates environment markers for the current interpreter and OS.
Run it on Windows to include Windows-only dependencies. It requires network
access to the advisory service. A clean result covers known advisories at the
time of the check; it is not a substitute for code review.
