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
| Full suite, plus the wheel installed clean and booted | Shipping something that does not run |
| A required reviewer on the `pypi` environment | Everything else |

The second gate is the one that makes `main` safe to push to. A README fix
merged to `main` starts the workflow, which reads the version, sees PyPI already
has it, and stops — no failure, no email, nothing published.

## One-time setup on GitHub

### 1. The environment and the token

**Settings → Environments → New environment**, name it exactly `pypi`.

Inside it:

- **Deployment protection rules → Required reviewers** → add yourself.
  This is the approval gate. Without it the pipeline publishes unattended.
- **Deployment branches and tags → Selected branches** → add `main`.
  Now the environment — and the token in it — cannot be reached from any other
  branch, even by a workflow edited on that branch.
- **Environment secrets → Add secret**
  - Name: `PYPI_API_TOKEN`
  - Value: your PyPI token, including the `pypi-` prefix

Put the token in the **environment**, not in repository secrets. A repository
secret is readable by any workflow on any branch; an environment secret is only
handed out after the protection rules pass.

Get the token from PyPI → **Account settings → API tokens**. Scope it to the
`flexi` project once the first release exists.

### 2. Branches

**Settings → General → Default branch** → switch to `dev`.

**Settings → Rules → New branch ruleset** for `main`:

- Require a pull request before merging
- Require status checks to pass: `Lint and types`, `The built wheel installs and runs`
- Block force pushes

### 3. Optional: drop the token entirely

PyPI supports [trusted publishing](https://docs.pypi.org/trusted-publishers/),
which swaps the token for a short-lived OIDC credential. There is then no secret
to leak or rotate.

On PyPI, add a trusted publisher for the `flexi` project: owner `ellsphillips`,
repository `flexi`, workflow `release.yaml`, environment `pypi`. Then in
`release.yaml`, give the `publish` job:

```yaml
    permissions:
      id-token: write
```

and replace the upload step with:

```yaml
      - run: uv publish --trusted-publishing always dist/*
```

Delete the `PYPI_API_TOKEN` secret afterwards.

## Testing the pipeline without publishing

Publish a release candidate to TestPyPI first by setting the version to
something like `0.2.0rc1` and pointing `UV_PUBLISH_URL` at
`https://test.pypi.org/legacy/`. Or run the whole thing on a fork.

## If a release goes wrong

You cannot overwrite a version on PyPI, and you should not delete one people may
already have installed. Yank it instead — PyPI → **Manage project → Releases →
Yank** — which hides it from new installs while leaving it resolvable for anyone
who pinned it. Then bump the version and release again.
