## What this changes

<!-- Why, not what. The diff already says what. -->

## How it was checked

<!-- The test that fails without this change. For a bug fix, say that you
     reverted the fix and watched the test go red — a test that passes either
     way is not a regression guard. -->

- [ ] `uv run pytest -q`
- [ ] `uv run pre-commit run --all-files`
- [ ] Screenshots regenerated (`uv run python scripts/shoot.py`) if the interface moved
