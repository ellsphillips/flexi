# Contributing

Use Python 3.12–3.14 and uv 0.9.26 or newer. CI pins its own uv version;
local development accepts newer releases.

```
uv sync
uv run pre-commit install
uv run pytest -q
```

`dev` is the default and working branch: branch off it, and open pull requests
into it. Release pull requests go from `dev` into `main`, with a title such as
`chore(release): 0.2.0`. Automation prepares the version, release notes, and
screenshots on `dev` before review and merge. See
[`docs/RELEASING.md`](docs/RELEASING.md) for the checks and publishing procedure.

To try the staged release with an authenticated GitHub CLI, run
`uv run -m scripts.try_release` from the checkout. It separately tests the
TestPyPI `flexi-test` wheel and the production `flexi` CI wheel, built from the
same source and version. Both retain the `flexi` import and command. Add `--demo`
to try the production CI wheel's interactive demo after both checks. See
[Try the staged release](docs/RELEASING.md#try-the-staged-release) for prerequisites;
production publishing still requires the owner's approval.

The hooks run CI's static checks — ruff, the formatter, mypy and
`uv lock --check` — through the locked environment. They do not run the suite.
Run `uv run pytest -q` before pushing. CI tests the proposed merge on pull
requests into `dev` or `main`, including the supported operating systems and
interpreters. A push without an open pull request does not start CI. To check a
pushed branch before opening one, use an authenticated GitHub CLI:

```bash
gh workflow run ci.yaml --ref YOUR_BRANCH
```

A manual run provides early feedback; the pull request still needs its own
passing checks. Merging a release into `main` starts the full release checks.

## The layout

```
src/flexi/
  domain/      pure functions and value objects — no Textual, no SQLAlchemy
  models/      the tables, the engine, backups, the lease, the migration runner
  migrations/  the Alembic revisions (0001..), found through alembic.ini
  services/    everything that touches the database
  cli/         the click commands and their prompts
  components/  widgets
  screens/     what the widgets are arranged into
  theme/       one stylesheet, and the palette parsed back out of it
  styles/      the per-screen stylesheets (dashboard, leave)
```

The layering is enforced by `tests/test_layering.py`, which walks the imports:
`domain` may not import Textual or SQLAlchemy, and `components` and `screens` may
not import SQLAlchemy. If a widget needs a number, a service works it out.

Two other rules the linter cannot see:

- **The system clock is read in one place.** `flexi.wallclock` is the only module
  that calls `date.today()` or `datetime.now()`; `DTZ005` and `DTZ011` are on
  everywhere else so this stays true.
- **A module never calls another module's `rebuild()`, and never writes.** It
  posts a message the screen handles — `BookHere`, `DeleteHere`,
  `BookRequested` — and the screen does the write, reports the result and calls
  `refresh_modules(scope)`. Each module declares in `WATCHES` which scopes
  redraw it.

## What a change comes with

A test that fails without it. For a bug, the test should fail against the old
behaviour — worth checking by reverting the fix and watching it go red, because a
test that passes either way is not a regression guard.

Durations are `timedelta`, never float hours: 7.4 is not representable in binary
floating point, and a leave year of rounding it gives a balance that disagrees
with the sum of its own rows.

Add a short user-facing note under `## Unreleased` in `CHANGELOG.md` for changes
that belong in future release notes. For the first 0.2.0 release, update its
existing `## 0.2.0` section instead. Preparation promotes `Unreleased` only when
the version named in the release pull request has no section yet.

Anything that changes the interface should regenerate the screenshots and their
text twins in `docs/shots/`:

```
uv run python scripts/shoot.py
```

The release preparer also regenerates them after updating the version drawn in
the header. Version bumps belong to the release pull request, not each feature
or fix.

## Style

`ruff` runs with `select = ALL`. The ignore list in `pyproject.toml` is short and
every entry carries its reason; if you need to add one, add the reason with it.

Docstrings are optional and one line is usually right. `D1` is switched off for
exactly that reason — write one when the code cannot say the thing itself, and
say what would surprise a reader, not what the signature already tells them.

Commit messages are conventional (`fix:`, `feat:`, `refactor:`, `docs:`, `ci:`,
`build:`, `test:`) and say why, not what — the diff already says what.
