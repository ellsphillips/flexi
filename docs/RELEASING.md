# Releasing

Work lands on `dev`. Merging a new version into `main` starts the release
workflow, which verifies it and waits for approval before publishing to PyPI.

## Publish a release

1. On `dev`, update the version in `pyproject.toml`, the README badge, and
   `CHANGELOG.md`.
2. Run `uv run python scripts/shoot.py` to update screenshots; the application
   header includes the version. Commit the changes.
3. Open a pull request from `dev` into `main` and wait for **All green**.
4. Merge the pull request. The release workflow runs the same static, test,
   and package checks as CI.
5. Approve the `pypi` deployment in GitHub Actions.
6. After publication, review and publish the draft GitHub release.

The workflow accepts only `main`, fails if PyPI cannot confirm whether the
version exists, and skips a version that is already published. The verified
wheel and source distribution are the artifacts uploaded to PyPI.

## One-time setup

### GitHub environment

Create an environment named `pypi` under **Settings → Environments**:

- Add a required reviewer.
- Restrict deployment branches to `main`.

The reviewer rule provides the approval gate. Merely naming the environment
in a workflow does not require approval.

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
a pull request, the **All green** status check, and protection against force
pushes. `All green` waits for every reusable CI workflow, including the matrix
jobs, so it is the single status check to require.

## Verify without publishing

CI runs on pull requests without publishing. It checks formatting, types,
dependency advisories, the OS/Python/timezone matrix, minimum dependency
versions, metadata, and clean wheel installs.

See [TESTING.md](TESTING.md) for local commands. To rehearse an upload, use
[TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/) with
its own account and credentials:

```bash
uv build
UV_PUBLISH_URL=https://test.pypi.org/legacy/ uv publish dist/*
```

The GitHub release workflow itself always targets PyPI.

## Recover a failed release

- **Before upload:** fix the failed check and rerun the workflow.
- **Upload succeeded but tagging failed:** use **Re-run failed jobs** on that
  run. A fresh run sees the published version and skips publication and tagging.
  Check any existing tag points to the release commit.
- **A published version is broken:** yank it in PyPI, bump the version, and
  release a fix. PyPI versions cannot be overwritten; deleting a release can
  disrupt existing installations.
