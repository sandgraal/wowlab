#!/usr/bin/env python3
"""PreToolUse(Bash): the hard stops the permission allowlist cannot express.

Blocks, with an explanation the agent can act on:
  * `git commit --no-verify` / `-n`
  * bare force-push (`--force`, `-f`); `--force-with-lease` is allowed
  * any push to main, deleting main
  * `gh pr merge --admin`; `gh api` mutating repo settings (PUT/PATCH/DELETE)
  * recursive+force `rm` outside build/scratch directories
  * shell writes (`>`, `>>`, `tee`, `sed -i`, `cp`/`mv` targets) into harness
    files, ADRs, the backlog, LICENSE, or merged migrations

Known gaps, documented in docs/AGENT_WORKFLOW.md: writes done from inside a
`python -c`, a heredoc-fed interpreter, or an editor cannot be inspected here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    SCRATCH_MARKERS,
    command_position,
    deny,
    read_hook_input,
    relative_to_checkout,
    shell_write_denial,
    split_segments,
)

EVENT = "PreToolUse"
MAIN_REFS = ("main", "HEAD:main", "refs/heads/main", ":main", "HEAD:refs/heads/main")


def _check_git(seg: list[str]) -> str | None:
    i = command_position(seg, "git")
    if i is None:
        return None
    args = seg[i + 1 :]
    sub = next((a for a in args if not a.startswith("-")), None)
    if sub == "commit" and ("--no-verify" in args or "-n" in args):
        return "--no-verify bypasses the lint gate and gitleaks. Fix the failure instead."
    if sub == "push":
        if "--force" in args or "-f" in args:
            return (
                "bare force-push; use --force-with-lease (and only over commits you have fetched)."
            )
        positional = [a for a in args[args.index("push") + 1 :] if not a.startswith("-")]
        if ("--delete" in args or "-d" in args) and "main" in positional:
            return "refusing to delete main."
        if ":main" in positional:
            return "refusing to delete main."
        for tok in positional:
            if tok in MAIN_REFS:
                return "direct push to main; open a PR."
    if sub == "branch" and ("-D" in args or "-d" in args) and "main" in args:
        return "refusing to delete main."
    return None


def _check_gh(seg: list[str]) -> str | None:
    i = command_position(seg, "gh")
    if i is None:
        return None
    args = seg[i + 1 :]
    if args[:2] == ["pr", "merge"] and "--admin" in args:
        return "--admin bypasses branch protection; satisfy the required checks and threads."
    if args[:1] == ["api"]:
        for flag in ("-X", "--method"):
            if flag in args:
                idx = args.index(flag) + 1
                method = args[idx].upper() if idx < len(args) else ""
                if method in ("PUT", "PATCH", "DELETE"):
                    return (
                        "gh api with a mutating method changes repo state; "
                        "repo settings go through scripts/bootstrap-github.sh, run by the conductor."
                    )
        joined = " ".join(args)
        has_body = any(a.startswith("-f") or a.startswith("-F") or a == "--input" for a in args)
        if "repos/" in joined and "graphql" not in args and has_body:
            return "gh api POST against a repo endpoint is reserved for the bootstrap script."
    return None


def _check_rm(seg: list[str]) -> str | None:
    i = command_position(seg, "rm")
    if i is None:
        return None
    args = seg[i + 1 :]
    recursive = force = False
    targets: list[str] = []
    for a in args:
        if a == "--recursive":
            recursive = True
        elif a == "--force":
            force = True
        elif a.startswith("--"):
            continue
        elif a.startswith("-"):
            if "r" in a or "R" in a:
                recursive = True
            if "f" in a:
                force = True
        else:
            targets.append(a)
    if recursive and force:
        for t in targets:
            if not any(m in t for m in SCRATCH_MARKERS):
                return (
                    f"recursive+force rm of {t!r} outside build/scratch dirs; "
                    "use git worktree remove / git clean, or ask."
                )
    return None


def _write_targets(seg: list[str]) -> list[str]:
    """Paths a segment writes to via redirects or common in-place tools."""
    targets: list[str] = []
    for j, tok in enumerate(seg):
        if tok in (">", ">>") and j + 1 < len(seg):
            targets.append(seg[j + 1])
    for tool in ("tee", "sed", "cp", "mv", "install", "truncate"):
        i = command_position(seg, tool)
        if i is None:
            continue
        args = seg[i + 1 :]
        if tool == "sed" and not any(a.startswith("-i") for a in args):
            continue
        positional = [a for a in args if not a.startswith("-")]
        if tool == "sed" and positional:
            positional = positional[1:]  # first positional is the script
        if tool in ("cp", "mv", "install") and positional:
            positional = positional[-1:]  # destination only
        targets.extend(positional)
    return targets


def main() -> None:
    data = read_hook_input()
    if data.get("tool_name") != "Bash":
        return
    command = str(data.get("tool_input", {}).get("command", ""))
    if not command:
        return
    cwd = str(data.get("cwd") or ".")
    for seg in split_segments(command):
        for check in (_check_git, _check_gh, _check_rm):
            reason = check(seg)
            if reason:
                deny(EVENT, reason)
        for target in _write_targets(seg):
            if target.startswith("/dev/") or target.startswith("$"):
                continue
            root, rel = relative_to_checkout(target, cwd)
            reason = shell_write_denial(rel, root)
            if reason:
                deny(EVENT, reason)


if __name__ == "__main__":
    main()
