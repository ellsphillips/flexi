# Releasing

`dev` is where work happens. `main` is what has been released. Nothing reaches
PyPI without a human pressing a button.

## The flow

```
feature branch ──PR──> dev ──PR──> main ──> build ──> [approve] ──> PyPI ──> tag
```

1. Branch off `dev`, open a pull request into `dev`. CI runs.
2. When you want to release, bump `version` in `pyproject.toml` on `dev`.
3. Open a pull request from `dev` into `main`. CI runs again.
4. Merge it. `release.yaml` starts.
5. It stops and waits for you. Approve it in the **Actions** tab.
6. It publishes, tags `vX.Y.Z`, and drafts a release.

## The four gates

Publishing is meant to be hard to do by accident. All four must pass:

| Gate | What it stops |
|---|---|
| `on: push: branches: [main]` | A release from any other branch |
| Version not already on PyPI | Republishing, and any push to `main` that isn't a version bump |
| `verify.yaml` — the same checks a pull request runs | Shipping something that does not run |
| A required reviewer on the `pypi` environment | Everything else |

The second gate is the one that makes `main` safe to push to. A README fix
merged to `main` starts the workflow, which reads the version, sees PyPI already
has it, and stops — no failure, no email, nothing published.

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

- Require a pull request before merging
- Require status checks to pass: **`All green`**, and nothing else
- Block force pushes

`All green` is the one check worth naming. The matrix jobs carry their
parameters in their names — `macos-latest · Python 3.14 · TZ UTC` — so
requiring them individually means editing this ruleset every time a row is
added, and the ruleset protecting less than you think until somebody does.
`All green` fails if any job in `verify.yaml` failed, and its name never
changes.

## Testing the pipeline without publishing

Publish a release candidate to TestPyPI first by setting the version to
something like `0.2.0rc1` and pointing `UV_PUBLISH_URL` at
`https://test.pypi.org/legacy/`. Or run the whole thing on a fork.

## If a release goes wrong

You cannot overwrite a version on PyPI, and you should not delete one people may
already have installed. Yank it instead — PyPI → **Manage project → Releases →
Yank** — which hides it from new installs while leaving it resolvable for anyone
who pinned it. Then bump the version and release again.
