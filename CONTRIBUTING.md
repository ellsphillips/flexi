# Contributing

```
uv sync
uv run pre-commit install
uv run pytest -q
```

`dev` is the working branch: branch off it, and open pull requests into it.
`main` is what has been released, and merging into it starts the release
pipeline — see [`docs/RELEASING.md`](docs/RELEASING.md).

The hooks run the same commands CI runs, through the same locked environment, so
a clean commit is a green pipeline.

## The layout

```
src/flexi/
  domain/      pure functions and value objects — no Textual, no SQLAlchemy
  models/      the schema, and the migrations that get you to it
  services/    everything that touches the database
  components/  widgets
  screens/     what the widgets are arranged into
  theme/       one stylesheet, and the palette parsed back out of it
```

The layering is enforced by `tests/test_layering.py`, which walks the imports:
`domain` may not import Textual or SQLAlchemy, and `components` and `screens` may
not import SQLAlchemy. If a widget needs a number, a service works it out.

Two other rules the linter cannot see:

- **The system clock is read in one place.** `flexi.wallclock` is the only module
  that calls `date.today()` or `datetime.now()`; `DTZ005` and `DTZ011` are on
  everywhere else so this stays true.
- **A module never calls another module's `rebuild()`.** It posts `DataChanged`
  with a scope, and whoever declared an interest redraws.

## What a change comes with

A test that fails without it. For a bug, the test should fail against the old
behaviour — worth checking by reverting the fix and watching it go red, because a
test that passes either way is not a regression guard.

Durations are `timedelta`, never float hours: 7.4 is not representable in binary
floating point, and a leave year of rounding it gives a balance that disagrees
with the sum of its own rows.

Anything that changes the interface should regenerate the screenshots:

```
uv run python scripts/shoot.py
```

## Style

`ruff` runs with `select = ALL`. The ignore list in `pyproject.toml` is short and
every entry carries its reason; if you need to add one, add the reason with it.

Docstrings are optional and one line is usually right. `D1` is switched off for
exactly that reason — write one when the code cannot say the thing itself, and
say what would surprise a reader rather than what the signature already tells
them.

Commit messages are conventional (`fix:`, `feat:`, `refactor:`, `docs:`, `ci:`,
`build:`, `test:`) and say why, not what — the diff already says what.
