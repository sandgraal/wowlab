# M16-32: PyPI publishing, owner checklist

Written 2026-11-13 by the implementer of the PyPI part of M16-32. The
workflow side is the `pypi` job in `.github/workflows/release.yml`; nothing
on pypi.org has been done yet. Until the steps below are done, the next `v*`
tag publishes the GitHub Release as before, and then the `pypi` job fails
(PyPI refuses an upload from a workflow it does not trust). Nothing else in
the run depends on it.

## What the job does

After `release` has published the GitHub Release, `pypi` downloads the
artifact `build` made, checks the wheel and the sdist against the SHA-256
values `build` recorded as job outputs, copies exactly those two files into
`dist/`, checks them again, and uploads them with
`pypa/gh-action-pypi-publish` (pinned to commit
`7245906fb61fa33a13554f9d5fb856fe61758086`, v1.14.3). It uses trusted
publishing: PyPI exchanges the job's OIDC token for an upload token that
lasts minutes. There is no API token and no repository secret. The action
also attaches PEP 740 attestations to the upload, and prints each file's
SHA-256 in the log, which is how the ticket's acceptance ("the PyPI page
shows v0.2 with the same SHA-256 as the GitHub asset") is read off.

The job runs in the `release` environment (`environment: release`) and holds
`id-token: write` and no other permission. Because it references the
environment, it waits for your approval like `release` does: a tag run now
asks you to approve twice, once for the GitHub Release and once, after it is
out, for PyPI. Approving `release` and then declining `pypi` publishes to
GitHub only.

## 1. Check the name on PyPI

The project name is `wowlab`, the `name` in `lab/core/pyproject.toml`.
On 2026-11-13, `https://pypi.org/pypi/wowlab-core/json` and
`https://pypi.org/pypi/wowlab/json` both returned 404 (no such project).
Check again before step 2: open `https://pypi.org/project/wowlab/`. A
404 page means the name is free. If it is taken, stop and tell the
conductor; the package would need a new name, which changes the wheel's
file name and the release checks.

**A pending publisher does not reserve the name.** The name is claimed only
by the first upload, which creates the project. Until then anyone can
register `wowlab` on PyPI first, and an install line pointing at it
would install their package. That is why this PR adds no PyPI line to
INSTALL.md or README.md: those lines are added after the first PyPI
release, once you have confirmed that `https://pypi.org/project/wowlab/`
exists and is yours.

Note on the ticket's wording: `uvx wowlab doctor` asks PyPI for a project
named `wowlab`. When this handoff was written the project was named
`wowlab-core`, and the working command would have been
`uvx --from wowlab-core wowlab doctor`.

*Decided 2026-11-13 (owner, in session):* the distribution is renamed from
`wowlab-core` to `wowlab`, so `uvx wowlab doctor` and `uvx wowlab ui` work
as typed. The import package stays `wowlab_core` and the command stays
`wowlab`. From the next release the wheel is
`wowlab-<version>-py3-none-any.whl` and the sdist `wowlab-<version>.tar.gz`;
v0.1.0 keeps its published `wowlab_core-0.1.0-...` names.

## 2. Add a pending trusted publisher on pypi.org

A pending publisher lets the first upload create the project.

1. Sign in to https://pypi.org (turn on two-factor authentication if it is
   not on; PyPI requires it).
2. Open **Your account → Publishing** (https://pypi.org/manage/account/publishing/).
3. Under **Add a new pending publisher**, choose **GitHub** and fill in:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `wowlab` |
   | Owner | `sandgraal` |
   | Repository name | `wowlab` |
   | Workflow name | `release.yml` |
   | Environment name | `release` |

4. Click **Add**.

**The environment name is not optional.** Enter `release`. PyPI lets you leave
it blank, and a blank field would let any job in `release.yml` that holds
`id-token: write` publish to PyPI. The `app_release` and
`windows_app_release` jobs hold `id-token: write` (for their attestations)
and run in no environment, so with a blank field either of them could upload
to PyPI without your approval. With `release` filled in, PyPI accepts only a
token from a job in the `release` environment, which needs your approval and
which only `v*` tags may deploy to.

## 3. After the first PyPI release

- The pending publisher becomes a normal publisher of the `wowlab`
  project. Check under the project's **Settings → Publishing** that it still
  names `release.yml` and the environment `release`.
- Compare the SHA-256 PyPI shows for the wheel (the file's **view hashes**
  link) with the wheel's line in the release's `SHA256SUMS`. They must be
  the same; the job's log prints it too.
- Run `uvx wowlab --version`.
- Then, in a follow-up PR, INSTALL.md and README.md gain the PyPI lines
  (`uvx wowlab doctor`, `uv tool install wowlab`). M16-32 stays open until
  they land. (The bare `uvx wowlab doctor` question above was decided on
  2026-11-13: the distribution is `wowlab`.) Beside those lines goes the
  upgrade note, because `uv tool install wowlab` over v0.1.0 stops with
  "Executable already exists: wowlab" and `uv tool upgrade wowlab-core`
  finds nothing to upgrade: "Installed v0.1.0 (wowlab-core)? Run `uv tool uninstall wowlab-core` first."
- The download count for the gate adds PyPI's figures from pypistats.org
  (ADR-0031, later amendment of 2026-10-21); that is the remaining
  `M13-gate.md` part of M16-32, not done here.

## Things this PR does not do

- No change to README.md or INSTALL.md (see above).
- A `v*` tag whose version is a pre-release or development version
  (`0.2.0rc1`, `0.2.0.dev1`) is published to PyPI too, as a PyPI
  pre-release, and **permanently**: PyPI never lets a file name be uploaded
  again, and deleting a release does not free its version. If you do not
  want a pre-release on PyPI, decline the `pypi` approval for that run.
- `lab/core/pyproject.toml` has no `readme` field, so the PyPI page will show
  no description, and `twine check` in the job warns about it (a warning,
  not a failure). Adding one changes the wheel's metadata and is a separate
  change.
- A re-run of `pypi` after a successful upload fails: PyPI never accepts a
  file name twice. That is expected; nothing needs repairing.
