# Testing

Four kinds of test, each answering a different question. A change is not done
until the kinds it touches are green.

| Kind | Question | Where | Speed |
|---|---|---|---|
| **Domain** | Is the arithmetic right? | `tests/domain/` | instant |
| **Service** | Does the write happen, and is it refused when it should be? | `tests/services/` | fast, in-memory SQLite |
| **Pilot** | Does the keypress do the thing? | `tests/tui/` | ~50 ms each |
| **Snapshot** | Does it still look right? | `tests/snapshot/` | ~100 ms each |

---

## 1. Domain tests

`flexi/domain/` has no I/O, so these are table-driven and there are a lot of them.
The three that matter most:

```python
@pytest.mark.parametrize(
    ("worked", "expected", "want"),
    [
        (h(7, 24), h(7, 24), "0:00"),
        (h(8, 12), h(7, 24), "+0:48"),
        (h(3, 10), h(7, 24), "−4:14"),   # U+2212, not a hyphen
    ],
)
def test_delta_formatting(worked, expected, want):
    assert format_delta(worked - expected) == want
```

`Period` gets a full matrix: every granularity × {start, end, label, shift(±1),
zoom, contains}, including the boundaries that break naive implementations —
31 January `shift(+1)` on `MONTH`, a `WEEK` spanning a year end, and a `YEAR`
whose leave year starts on `04-06`.

`punch.buckets()` is tested against exact expected strings, because the strip is
the signature and a one-cell drift is invisible in review and obvious in use.

## 2. Service tests

`tests/conftest.py` already provides an in-memory session; keep it. Add a
`services` fixture returning a built `Services` registry, and a `frozen_clock`
fixture using `time-machine`:

```python
@pytest.fixture
def at():
    def _at(iso: str):
        return time_machine.travel(iso, tick=False)
    return _at

def test_clock_in_refused_on_absence(services, at):
    with at("2026-06-10 09:00:00+01:00"):
        services.absence.book(date(2026, 6, 10), AbsenceType.ANNUAL, Portion.FULL)
        result = services.clock.clock_in()
    assert not result.success
    assert "annual" in result.message.lower()
```

Every service method that returns a `Result` needs both branches tested. The
refusal message is part of the contract — it is what the status bar shows.

**Migrations.** `tests/models/test_migrations.py` upgrades and downgrades a
*populated* database. `0007` rebuilds `absence_days` rather than altering it, and
a table rebuild that silently loses rows is the kind of bug only discovered by the
person whose leave records it ate.

Write to those tables with **raw SQL, not the ORM**: the models carry columns a
later revision adds, so writing through them tests the schema against itself
rather than against what is on disk.

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
already saved, so the setup screen does not intercept. Put it in
`tests/tui/conftest.py`.

What to cover here, at minimum — these are the acceptance tests for the six
features:

- `/` clocks in and out from every screen, and does **not** fire inside an
  `Input`.
- `space` expands the row under the cursor and the cursor does not move.
- `d`/`w`/`m`/`y` change the records table's row count to 1 / 7 / 28–31 / 12.
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

`tests/snapshot/test_screens.py` drives eleven screens and compares what the
compositor produced against the text committed in `docs/shots/`.

**Text, not SVG.** `pytest-textual-snapshot` is installed and compares rendered
SVGs, which are only readable as pictures — a CI failure becomes a file you have
to download before you can tell whether the change was intended. Comparing
characters means a failure prints a unified diff of two screens, in the terminal,
where whoever caused it is already looking:

```
-  │ ANNUAL LEAVE  20.5 left of 25 │
+  │ ANNUAL LEAVE  19.5 left of 25 │
```

The SVGs are still written and are still what a reviewer looks at; they are just
not what the test asserts on.

Rules that keep them useful rather than noisy:

- **Freeze time.** Every case runs inside `time_machine.travel` at
  `flexi.services.samples.NOW` against the seeded database, otherwise the diff is
  the clock.
- **Three widths, every time**: 120×36 (wide), 84×28 (narrow), 64×22 (tiny). The
  responsive rules in `DESIGN-SYSTEM.md` §6 only exist if all three are pinned.
- **Update deliberately**: `uv run python scripts/shoot.py` regenerates both the
  SVGs and the text, and the diff is what you review before committing it.

## 5. Screenshots for review

Snapshots are for regression. For "show me what it looks like", drive the real
app headlessly and export SVG:

```python
# scripts/shoot.py
async def shoot(name: str, size: tuple[int, int], keys: list[str]) -> None:
    app = build_demo_app()
    async with app.run_test(size=size) as pilot:
        for key in keys:
            await pilot.press(key)
        await pilot.pause()
        app.save_screenshot(f"docs/shots/{name}.svg")
```

`uv run python scripts/shoot.py` writes the set into `docs/shots/`, as an SVG and
a text twin per screen. Convert to PNG for a terminal that renders images:

```
rsvg-convert -w 1600 docs/shots/dashboard-wide.svg -o /tmp/dashboard-wide.png
```

The demo seeds a leave year of plausible data — some overtime, one short day, a
booked week of annual leave, two bank holidays, a sick day, a half-day and a TOIL
day — so the shots show the interesting cases rather than an empty database. It
lives in `flexi/services/samples.py` and is what `flexi --demo` runs, which makes
it the same data a reviewer, a snapshot test and a new user all see.

`--demo` builds it in a temporary directory and throws it away on exit, so it can
never be confused with real records.

## 6. Running

```
uv run pytest -q                     # everything
uv run pytest tests/domain -q        # while working on arithmetic
uv run pytest tests/tui -q           # while working on interaction
uv run python scripts/shoot.py       # after an intentional visual change
uv run mypy                          # strict, and it is meant to stay strict
uv run ruff check
```

Neither `mypy` nor `ruff` needs an argument. `mypy` covers `src` and `tests`
both, and `mypy_path = "src"` is what stops a src layout being discovered twice
— once as `flexi.x` and once as `src.flexi.x` — which makes mypy refuse to check
either.

`uv run pre-commit run --all-files` runs the lot, and those hooks are the same
commands CI runs.

A failed snapshot prints its diff, so nothing needs uploading as an artifact.
