# Releasing

Work lands on `dev`, the default branch. A release pull request from `dev` into
`main` names the next version; automation prepares the release on `dev` for
review. Merging starts the release checks; publishing waits for the owner's
approval in GitHub Actions.

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
6. As `ellsphillips`, open the release run in GitHub Actions and click **Review
   deployments**. Select the **pypi** checkbox, then **Approve and deploy**. The
   workflow uploads the checked wheel and source distribution only after this
   approval.
7. After publication, review and publish the draft GitHub release.

Preparation promotes `## Unreleased` when the requested version has no changelog
section yet. An existing version section is preserved; update its notes directly.
Screenshots run in an isolated job with read-only repository permissions;
running application code does not require repository write credentials.

The publishing workflow accepts only `main` and fails if PyPI cannot confirm
whether the version exists. The artifacts that pass the package checks are the
ones uploaded to PyPI.

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

### GitHub environment

Create an environment named `pypi` under **Settings → Environments**:

- Set `ellsphillips` as the only required reviewer.
- Allow self-review so the owner can approve a release triggered by their merge.
- Disable administrator bypass.
- Restrict deployment branches to `main`.

The reviewer rule provides the manual approval gate immediately before upload.
Merely naming the environment in a workflow does not require approval.

### PyPI trusted publisher

In the project's PyPI settings, add a GitHub trusted publisher:

| Field | Value |
|---|---|
| Owner | `ellsphillips` |
| Repository | `flexi` |
| Workflow | `release.yaml` |
| Environment | `pypi` |

[Trusted publishing](https://docs.pypi.org/trusted-publishers/) uses short-lived
credentials. The workflow does not need a stored PyPI API token.

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

See [TESTING.md](TESTING.md) for the full local checks. To rehearse an upload, use
[TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/) with
its own account and credentials:

```bash
uv build
UV_PUBLISH_URL=https://test.pypi.org/legacy/ uv publish dist/*
```

The GitHub release workflow itself always targets PyPI.

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
- **Only part of the upload succeeded, or the tag or draft release is missing:**
  retry the original run. Existing PyPI files must have the same SHA256 digests
  as the tested artifacts. Matching files are skipped during upload; missing
  files can be uploaded. An existing tag must point to the same commit, and an
  existing GitHub release is reused without replacing it.
- **A new run sees a completed version:** when both distributions, the tag, and
  a draft or published GitHub release exist, it does nothing. Later documentation
  commits therefore do not republish that version. A new run can resume an
  incomplete release only when its artifacts and any existing tag still match;
  use the original run for recovery, not a later `main` commit.
- **The original artifacts are unavailable or their digests differ:** do not
  replace published files or move the release tag. Release a new version.
- **A published version is broken:** yank it in PyPI, bump the version, and
  release a fix. PyPI versions cannot be overwritten; deleting a release can
  disrupt existing installations.
