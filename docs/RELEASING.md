# Releasing

`dev` is where work happens. `main` is what has been released. Nothing reaches
PyPI without a human pressing a button.

## The flow

```
feature branch ──PR──> dev ──PR──> main ──> build ──> [approve] ──> PyPI ──> tag
```

1. Branch off `dev`, open a pull request into `dev`. CI runs.
2. When you want to release, on `dev`:
   - bump `version` in `pyproject.toml`;
   - update the version badge at the top of `README.md`;
   - run `uv run python scripts/shoot.py` and commit the shots.

   The suite asserts all three. The badge is checked by
   `tests/test_packaging.py::test_the_readme_version_badge_matches_the_project`,
   and the version is drawn in the application header, so every `.txt` twin in
   `docs/shots/` carries it and `tests/snapshot/` goes red without a re-shoot.
   Update `CHANGELOG.md` in the same commit.
3. Open a pull request from `dev` into `main`. CI runs again.
4. Merge it. `release.yaml` starts.
5. It stops and waits for you. Approve it in the **Actions** tab.
6. It publishes to PyPI, tags `vX.Y.Z`, and creates a **draft** GitHub release
   with generated notes.
7. Read the notes and publish the draft under **Releases**. Until you do, the
   repository shows no latest release.

## The four gates

Publishing is meant to be hard to do by accident. All four must pass:

| Gate | What it stops |
|---|---|
| The ref is `main` | A release from any other branch. `workflow_dispatch` accepts any ref, so the `guard` job checks `GITHUB_REF` itself, and the `pypi` environment's deployment-branch policy refuses the deployment as well. |
| Version not already on PyPI | Republishing, and any push to `main` that is not a version bump. |
| `static.yaml`, `tests.yaml`, `package.yaml` — the same three a pull request runs | Shipping something that does not run. |
| A required reviewer on the `pypi` environment | Everything else. |

The second gate is the one that makes `main` safe to push to. A README fix
merged to `main` starts the workflow, which reads the version, sees PyPI already
has it, and stops — no failure, no email, nothing published. A PyPI reply that is
neither 200 nor 404 stops the run instead of assuming the version is new.

## One-time setup on GitHub

### 1. The environment and the trusted publisher

**Settings → Environments → New environment**, name it exactly `pypi`.

Inside it:

- **Deployment protection rules → Required reviewers** → add yourself.
  This is the approval gate. Without it the pipeline publishes unattended.
- **Deployment branches and tags → Selected branches** → add `main`.
  Now the environment cannot be reached from any other branch, even by a
  workflow edited on that branch.

Then on PyPI — **Manage project → Publishing → Add a new publisher** — add a
GitHub publisher for `flexi`:

| Field | Value |
|---|---|
| Owner | `ellsphillips` |
| Repository | `flexi` |
| Workflow name | `release.yaml` |
| Environment | `pypi` |

That is [trusted publishing](https://docs.pypi.org/trusted-publishers/): the
`publish` job asks GitHub for a short-lived OIDC token and PyPI exchanges it for
a credential good for one upload. There is no `PYPI_API_TOKEN` to leak, rotate
or accidentally scope too widely, and the three fields above are what stop a
workflow added on some other branch from minting one.

If a `PYPI_API_TOKEN` secret still exists from the token era, delete it — the
workflow no longer reads it.

### 2. Branches

**Settings → General → Default branch** → switch to `dev`.

**Settings → Rules → New branch ruleset** for `main`:

- Enforcement: **Active**. A ruleset left disabled protects nothing.
- Require a pull request before merging
- Require status checks to pass: **`All green`**, and nothing else
- Block force pushes

`All green` is the one check worth naming. The matrix jobs carry their
parameters in their names — `macos-latest · Python 3.14 · TZ UTC` — so
requiring them individually means editing this ruleset every time a row is
added, and the ruleset protecting less than you think until one is.
`All green` fails if any job in any of the three failed, and its name never
changes.

### 3. The name on PyPI

`flexi` on PyPI was taken in 2022 by an unrelated package at `0.1.0`, which
ships no console script. Until it is yanked, `uvx flexi` on an interpreter older
than 3.12 resolves to it, downloads pandas, and fails with "Package `flexi` does
not provide any executables".

Yank it — **Manage project → Releases → 0.1.0 → Yank** — so resolvers skip it and
a pre-3.12 user gets `flexi==0.2.0 requires Python >=3.12` instead. This is
independent of the release: the guard only asks PyPI about `0.2.0`.

## Testing the pipeline without publishing

There is no TestPyPI mode. The workflow takes no inputs, and the PyPI host, the
`--check-url` and the trusted publisher are all fixed.

What you can rehearse: push the branch to a fork and run `release.yaml` there
from `main`. `guard`, the three verification workflows and `artefact` all run;
`publish` then fails at the trusted-publishing exchange, because the publisher is
bound to `ellsphillips/flexi`. A red `publish` step is the expected end of that
rehearsal, and everything before it is the part worth checking.

To rehearse the upload itself, do it locally and outside the pipeline:

```
uv build
UV_PUBLISH_URL=https://test.pypi.org/legacy/ uv publish dist/*
```

with a TestPyPI API token. That exercises `uv` and the artefact, not the
workflow.

Adding a real TestPyPI mode would mean a `workflow_dispatch` input choosing the
index, deriving the guard host, `UV_PUBLISH_URL` and `UV_PUBLISH_CHECK_URL` from
it, a second environment with its own trusted publisher on test.pypi.org, and
skipping the `tag` job so a release candidate does not push `v0.2.0rc1`.

## If a release goes wrong

You cannot overwrite a version on PyPI, and you should not delete one people may
already have installed. Yank it instead — PyPI → **Manage project → Releases →
Yank** — which hides it from new installs while leaving it resolvable for anyone
who pinned it. Then bump the version and release again.
