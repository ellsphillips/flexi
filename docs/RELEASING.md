# Releasing

Work lands on `dev`, the default branch. A release pull request from `dev` into
`main` names the next version; automation prepares the release on `dev` for
review. Merging runs the full checks, builds the release artifacts, and
automatically publishes that version to TestPyPI. After verification, publishing
the same files to PyPI waits for the owner's approval in GitHub Actions.

## Publish a release

1. Finish the release's changes on `dev`. For this first release, update the
   existing `## 0.2.0` notes in `CHANGELOG.md`. For future versions, collect notes
   under `## Unreleased`.
2. Open a pull request with **base `main`**, **head `dev`**, and the exact title
   **`chore(release): 0.2.0`** for this release. For later releases, replace
   `0.2.0` with a stable `X.Y.Z` version newer than the version on `main`. The
   title must match exactly: no `v` prefix, prerelease suffix, leading zeroes,
   or surrounding whitespace.
3. Wait for **Prepare release** (`release-prepare.yaml`). It updates
   `pyproject.toml`, `uv.lock`, the README version badge, and `CHANGELOG.md`, then
   regenerates the screenshots and their text twins. Those changes are committed
   to `dev` and appear in the same pull request. Review them with the application
   changes.
4. Wait for **Release prepared** and **All green** on the latest pull-request
   commit. New commits need fresh passing checks.
5. Use **Create a merge commit** to merge into `main`. Keep `dev`; regular merges
   preserve the ancestry between the development and release branches.
6. Wait for the full release checks, package build, automatic TestPyPI upload,
   and TestPyPI verification. Verification waits up to two minutes for the
   uploaded files to appear and requires both distribution filenames and SHA256
   digests to match the tested artifacts. Any failure stops the release before
   production. Run `uv run -m scripts.try_release --demo` locally to wait for
   staging, check the installed package, and try it before approving production.
   Omit `--demo` for automated checks only; see [Try the staged release](#try-the-staged-release).
7. As `ellsphillips`, open the release run in GitHub Actions and click **Review
   deployments**. Select the **pypi** checkbox, then **Approve and deploy**. The
   workflow uploads the same wheel and source distribution to PyPI without
   rebuilding or changing their version.
8. After publication, the workflow creates the version tag and draft GitHub
   release. Review and publish the draft.

Preparation promotes `## Unreleased` when the requested version has no changelog
section yet. An existing version section is preserved; update its notes directly.
Screenshots run in an isolated job with read-only repository permissions;
running application code does not require repository write credentials.

The publishing workflow accepts only `main` and stops if either package index
cannot confirm its release state. The artifacts that pass the package checks
are the ones uploaded to both indexes.

## Rerun preparation

Preparation starts automatically, including for the first 0.2.0 release.
GitHub's `pull_request_target` trigger uses the trusted workflow revision from
the default branch, `dev`.

To retry manually, open **Prepare release → Run workflow** in GitHub Actions,
select **dev**, and enter the pull-request number in **pull_request**. With the
GitHub CLI, replacing `123` with that number:

```bash
gh workflow run release-prepare.yaml --ref dev -f pull_request=123
```

This prepares the pull request; it does not publish to PyPI. Review any generated
commit and wait for both required checks before merging.

## One-time setup

### Actions event policy

Review **Settings → Actions → Policies**. GitHub's
[default policy](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target#default-policy-for-pull_request_target)
will block `pull_request_target` in affected public repositories from
2 November 2026. If preparation is blocked, use the manual retry above.
Automatic runs require an applicable policy explicitly allowing this event;
scope any exception to this repository's `.github/workflows/release-prepare.yaml`.

### Release-preparer GitHub App

The preparer uses a GitHub App to commit generated changes to `dev`. An App
commit starts the ordinary pull-request checks on the new commit.

1. Create a GitHub App with repository **Contents: Read and write** and
   **Pull requests: Read-only** permissions. The workflow rechecks the release
   pull request before committing. Disable webhooks if the App has no other use.
2. Install the App on the `ellsphillips/flexi` repository only.
3. In the repository's **Settings → Secrets and variables → Actions**, add the
   App's numeric ID as the variable **`RELEASE_APP_ID`**.
4. Generate a private key in the App's settings and store the full PEM file as
   the repository secret **`RELEASE_APP_PRIVATE_KEY`**.

The workflow creates a short-lived installation token with contents write and
pull-request read permissions for the commit step. Screenshot generation runs
separately without that token. A preparation run that finds no changes does not
need App credentials; a run that needs to commit changes reports missing
credentials and stops until they are configured.

### GitHub environments

Create an environment named `testpypi` under **Settings → Environments**.
Restrict deployment branches to `main` and leave required reviewers unset so
TestPyPI publishing runs automatically.

Create an environment named `pypi` under **Settings → Environments**:

- Set `ellsphillips` as the only required reviewer.
- Allow self-review so the owner can approve a release triggered by their merge.
- Disable administrator bypass.
- Restrict deployment branches to `main`.

The reviewer rule provides the manual approval gate after TestPyPI verification
and immediately before the production upload. Merely naming the environment in
a workflow does not require approval.

### TestPyPI and PyPI trusted publishers

[TestPyPI has separate accounts and project ownership from PyPI](https://packaging.python.org/en/latest/guides/using-testpypi/).
Register or sign in at [test.pypi.org](https://test.pypi.org/account/register/)
and sign in separately at [pypi.org](https://pypi.org/).

On **each index**, configure a GitHub trusted publisher for `flexi`:

1. If `flexi` already exists, its owner must add the publisher under
   **Manage project → Publishing**. Owning the PyPI project does not grant
   ownership of the TestPyPI project. See
   [adding a publisher to an existing project](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
2. If the name is available, add a
   [pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
   under **Your account → Publishing**, using project name `flexi`. This creates
   the project on first upload; it does not reserve the name beforehand.

| Field | TestPyPI | PyPI |
|---|---|---|
| GitHub owner | `ellsphillips` | `ellsphillips` |
| Repository | `flexi` | `flexi` |
| Workflow filename | `release.yaml` | `release.yaml` |
| Environment | `testpypi` | `pypi` |

[Trusted publishing supports both indexes](https://docs.pypi.org/trusted-publishers/using-a-publisher/#publishing-to-indices-other-than-pypi)
with short-lived credentials. Do not store a PyPI or TestPyPI API token in
GitHub; configure both publishers before merging the release.

### Branch protection

Set the default branch to `dev`. Create an active ruleset for `main` requiring
a pull request, the **Release prepared** and **All green** status checks, and
protection against force pushes. `All green` waits for every reusable CI
workflow, including the matrix jobs; `Release prepared` verifies that the
metadata and text snapshot versions match the release title. That read-only
check is defined in `release-check.yaml` and accepts only release pull requests
from this repository's `dev` branch into `main`.

Enable merge commits and use them for `dev` → `main` releases. Do not squash,
rebase, or delete `dev` after a release.

## Verify without publishing

CI runs on pull requests without publishing. It checks formatting, types,
dependency advisories, the OS/Python/timezone matrix, minimum dependency
versions, metadata, and clean wheel installs.

To inspect preparation locally:

```bash
uv run python scripts/prepare_release.py prepare --root . --title "chore(release): 0.2.0"
uv run python scripts/shoot.py
uv run python scripts/prepare_release.py check --root . --title "chore(release): 0.2.0" --check-snapshots
git diff
```

`prepare` updates the four metadata files; `check` verifies their agreement, and
`--check-snapshots` also checks the version drawn in the text snapshot headers.
Neither command commits, pushes, or publishes. Screenshot generation is a
separate step, also run by the preparation workflow.

See [TESTING.md](TESTING.md) for the full local checks. After merging, the release
workflow automatically rehearses publication on TestPyPI using the exact
production version and artifacts. Production remains paused until the owner
approves the `pypi` deployment.

## Try the staged release

From a checkout with `uv` installed and `gh` authenticated, run:

```bash
uv run -m scripts.try_release
```

If needed, sign in to GitHub once with `gh auth login`.

The TestPyPI stages of `release.yaml` must already be merged into remote `main`,
and the [one-time setup](#one-time-setup) must be complete. The command reuses a
release run for the current remote `main` commit, or starts one on `main`. It
waits for TestPyPI verification without waiting for production approval.

It downloads that run's exact distribution artifact and verifies the TestPyPI
wheel's SHA256 against it, then tests the installed version, CLI help,
import origin, dependencies, and headless TUI startup. The checks use a temporary
virtual environment, configuration, and data directory, with dependencies from
real PyPI. Temporary files are removed on exit.

Add `--demo` to launch the staged app's interactive demo after the checks; this
needs a terminal. Use `--run RUN_ID` to select or resume an exact release run.
The command never approves production; the owner must still approve the `pypi`
deployment separately.

## Maintaining the preparer

`scripts/release_pr.py` validates GitHub responses in `GitHubClient` and passes
immutable release requests and file bundles into the workflow. `plan` reads the
release request; `review` verifies the generated files without writing to GitHub;
`commit` accepts only those reviewed changes and rechecks the request before
advancing `dev`. Keep these stages separate when changing the automation.

The tests inject a typed repository client and cover malformed responses,
unexpected file changes, and concurrent pushes:

```bash
uv run pytest tests/test_release_pr.py tests/test_prepare_release.py tests/test_pipelines.py
```

## Recover a failed release

- **Preparation failed:** fix the reported problem on `dev` and rerun the
  preparer. Keep the same release pull request and review its updated diff.
- **A transient check or upload failed:** use **Re-run failed jobs** on the
  original release run. This retains its release commit and tested artifacts.
  If a code change is needed, take it through a new release pull request.
- **TestPyPI setup or verification failed:** fix the account, publisher, or
  reported verification problem, then retry the original run. Production stays
  blocked until TestPyPI verification passes.
- **Only part of the upload succeeded, or the tag or draft release is missing:**
  retry the original run. On either index, existing files must have the same
  SHA256 digests as the tested artifacts. Matching files are skipped during
  upload; missing files can be uploaded. An existing tag must point to the same
  commit, and an existing GitHub release is reused without replacing it.
- **A new run sees a completed version:** when both distributions exist on
  PyPI alongside the tag and a draft or published GitHub release, it does
  nothing. Later documentation commits therefore do not republish that version.
  A new run can resume an incomplete release only when its artifacts and any
  existing tag still match; use the original run for recovery, not a later
  `main` commit.
- **The original artifacts are unavailable or their digests differ:** do not
  replace published files or move the release tag. Release a new version,
  including when only TestPyPI contains the conflicting files. Both
  [TestPyPI](https://test.pypi.org/help/#file-name-reuse) and
  [PyPI](https://pypi.org/help/#file-name-reuse) reject reuse of distribution
  filenames, even after deletion. A collision must not bypass the TestPyPI gate.
- **A published version is broken:** yank it in PyPI, bump the version, and
  release a fix. PyPI versions cannot be overwritten; deleting a release can
  disrupt existing installations.
