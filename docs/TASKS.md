# Developer tasks

Use Python 3.12–3.14, uv 0.9.26 or newer, and just 1.46 or newer.
[Install just](https://just.systems/man/en/installation.html) with uv or your
operating system's package manager. These tools are for working on a checkout;
installing and using Flexi does not require just.

From the repository, start with:

```bash
uv tool install "rust-just>=1.46"
just setup
just check
just test
```

`just` without a recipe lists the available tasks. Recipes run from the
repository root. Arguments after `run` go to Flexi; arguments after the test
recipes go to pytest, for example `just test tests/domain -q`.
Use `just --show RECIPE` to inspect the commands behind a task.

| Recipe | Purpose | Arguments and effects |
|---|---|---|
| `just setup` | Prepare the development environment. | Syncs locked development dependencies and installs Git hooks. |
| `just run [ARGS…]` | Run Flexi from the checkout. | Uses your real configuration and database; accepts normal Flexi arguments. |
| `just demo` | Explore the app with sample records. | Uses temporary demo data. |
| `just dev` | Run with Textual's development tools. | Uses `scripts/dev.py`, migrates and opens your real database. |
| `just console` | Open Textual's development console. | Run beside `just dev` in another terminal. |
| `just check` | Check the lockfile, style, types, and workflows. | Runs checks without applying source fixes; does not run tests or the audit. |
| `just lint` | Check Python and justfile style. | Ruff checks, Python formatting checks, and justfile formatting checks. |
| `just types` | Check native and Windows typing. | Runs mypy in both platform views. |
| `just fix` | Apply automated style fixes. | May change Python files and the justfile; review the diff. |
| `just hooks` | Run all pre-commit hooks. | May modify files; review the diff. |
| `just test [ARGS…]` | Run the test suite or selected tests. | Accepts pytest paths and options. |
| `just test-ci [ARGS…]` | Run with CI's property-test budget. | Sets `HYPOTHESIS_PROFILE=ci`; accepts pytest arguments. |
| `just coverage [ARGS…]` | Measure and enforce test coverage. | Uses the CI profile, records coverage, and reports uncovered lines; accepts pytest arguments. |
| `just test-late [ARGS…]` | Exercise delayed UI callbacks. | Uses the CI profile and `FLEXI_LATE_CALLBACKS=0.05`; accepts pytest arguments. |
| `just test-floors [ARGS…]` | Test minimum dependency versions on the oldest supported Python, 3.12. | Copies the current checkout, including uncommitted changes, into temporary storage; keeps floor resolution out of your lockfile and `.venv`. uv selects or downloads Python 3.12 automatically. Accepts pytest arguments. |
| `just shots` | Regenerate interface screenshots. | Updates the tracked SVG and text files in `docs/shots/`. |
| `just audit` | Check locked dependencies for advisories. | Uses temporary requirements and a pinned pip-audit; needs network access. |
| `just workflow-check` | Validate GitHub Actions workflows. | Runs actionlint. |
| `just build` | Build the production wheel and source archive. | Replaces generated `dist/` with fresh artifacts. |
| `just build-staging` | Build production and a local TestPyPI preview. | Replaces generated `dist/` and `test-dist/`; does not publish. |
| `just package-check` | Check both distributions as installed packages. | Builds in temporary storage, checks metadata with Twine, and runs runtime smoke and installed-package tests in isolated environments. |
| `just release-prepare VERSION` | Prepare a release locally. | Updates metadata and screenshots, then checks them; does not commit or publish. |
| `just release-check VERSION` | Check prepared release metadata. | Checks the version, release notes, badge, lockfile, and text snapshot versions. |
| `just try-release [ARGS…]` | Try the exact staged and production CI wheels. | Reuses or dispatches a release on remote `main`; can publish to TestPyPI, but never approves production. Accepts `--run RUN_ID` and `--demo`. |
| `just ci BRANCH` | Request remote CI before opening a PR. | Requires authenticated `gh`; dispatches CI for a pushed branch. |
| `just release-retry PR_NUMBER` | Retry release preparation. | Requires authenticated `gh`; dispatches the preparer on `dev`, which may commit generated changes to the release PR. |

The remote recipes require the GitHub CLI; sign in with `gh auth login`.
`try-release` also requires the publisher and environment setup described in
[Releasing](RELEASING.md#one-time-setup). Its `--run` argument is a GitHub Actions
run ID, not a package version:

```bash
just try-release --run 35583429416 --demo
```

That ID comes from the end of the
[release run URL](https://github.com/ellsphillips/flexi/actions/runs/35583429416).
Omit `--run` to reuse or start a run for current remote `main`. `--demo` requires
a terminal and opens the production CI wheel's demo after both package checks.
TestPyPI uses a preview version such as `0.2.0.dev123`, with the run ID after
`.dev`; production stays `0.2.0`. A retry of the same run reuses its preview.
The older run above used a stable TestPyPI version and can still be checked.
Local `build-staging` and `package-check` use `.dev1` for their disposable preview.

See [Testing](TESTING.md) for test design and platform reproduction, and
[Releasing](RELEASING.md) for the publication gates. CI keeps its verification
commands in the workflow files and separately tests the recipes on all three
operating systems.

The recipes reuse the scripts in `scripts/`. `release_pr.py` and
`release_status.py` remain workflow internals: they depend on GitHub's event,
permission and approval context. `release_probe.py` and `smoke.py` are shared
installation checks, called by the package and release tasks. There is no
local recipe for approving or uploading a production release.
