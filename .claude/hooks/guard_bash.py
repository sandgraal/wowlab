#!/usr/bin/env python3
"""PreToolUse(Bash): the hard stops the permission allowlist cannot express.

Blocks, with an explanation the agent can act on:
  * `git commit --no-verify` in any spelling (`-n`, `-nm`, `--no-veri…`);
    `git -c core.hooksPath=…` (hook bypass)
  * force-push in any spelling (`--force`, `-f`, `-fu`, `+refspec`, `--mirror`);
    `--force-with-lease` is allowed
  * any push whose destination is main (`main`, `HEAD:main`, `x:main`,
    `refs/heads/main`, `--all`), deleting main, refspecs containing variables
  * `gh pr merge --admin`; `gh api` with a mutating method (`-X PUT`, `-XPUT`,
    `--method=DELETE`) or a GraphQL mutation other than the two review-thread ones
  * recursive+force `rm`, `find -delete`, `git clean -x` outside scratch dirs
  * shell writes, deletes and moves touching harness files, ADRs, the backlog,
    LICENSE, or merged migrations: redirects (`>`, `>>`, `>|`), `tee`,
    `sed -i`/`--in-place`, `cp`/`mv`/`install`/`ln`/`rsync` (including directory
    destinations), `dd of=`, `patch`, `rm`, `git rm`, `git checkout`/`git restore`
    pathspecs — through `bash -c`, `eval`, `cd`, `$CLAUDE_PROJECT_DIR`/`$PWD`,
    symlinks, and case changes

Fail-closed: an untokenisable command, an unparseable payload, an unresolvable
variable in a path, or an internal error denies the call. Known gaps, documented
in docs/AGENT_WORKFLOW.md: writes done from inside `python -c`, a heredoc-fed
interpreter, or an editor cannot be inspected here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    command_position,
    deny,
    expand_vars,
    git_subcommand,
    inner_commands,
    is_scratch_path,
    long_flag_present,
    read_hook_input,
    relative_to_checkout,
    shell_write_denial,
    short_flag_present,
    split_segments,
)

EVENT = "PreToolUse"
_ALLOWED_MUTATIONS = ("addPullRequestReviewThreadReply", "resolveReviewThread")


def _refspec_dst(spec: str) -> tuple[str, bool]:
    """Destination ref of a push refspec and whether it carries a leading '+' (force)."""
    force = spec.startswith("+")
    spec = spec.lstrip("+")
    dst = spec.split(":", 1)[1] if ":" in spec else spec
    if dst.startswith("refs/heads/"):
        dst = dst[len("refs/heads/") :]
    return dst, force


def _check_git(seg: list[str]) -> str | None:
    i = command_position(seg, "git")
    if i is None:
        return None
    sub, globals_, args = git_subcommand(seg[i + 1 :])
    if any(g.startswith("core.hooksPath") for g in globals_):
        return "git -c core.hooksPath=… disables the commit hooks. Fix the failure instead."
    if sub == "commit" and (
        long_flag_present(args, "--no-verify", 6) or short_flag_present(args, "n")
    ):
        return "--no-verify bypasses the lint gate and gitleaks. Fix the failure instead."
    if sub == "push":
        if (
            long_flag_present(args, "--force", 7) and "--force-with-lease" not in args
        ) or short_flag_present(args, "f"):
            return (
                "bare force-push; use --force-with-lease (and only over commits you have fetched)."
            )
        if "--mirror" in args or "--all" in args:
            return (
                "git push --all/--mirror can publish main; push the one branch you are working on."
            )
        positional = [a for a in args if not a.startswith("-")]
        if ("--delete" in args or short_flag_present(args, "d")) and "main" in positional:
            return "refusing to delete main."
        for spec in positional:
            if "$" in spec:
                return "variables in a push refspec cannot be checked; use literal refspecs."
            dst, force = _refspec_dst(spec)
            if force:
                return "'+refspec' is a force-push; use --force-with-lease."
            if dst == "main":
                return (
                    "refusing to delete main."
                    if spec.startswith(":")
                    else "direct push to main; open a PR."
                )
    if sub == "branch" and ("-D" in args or "-d" in args) and "main" in args:
        return "refusing to delete main."
    if sub == "clean" and (short_flag_present(args, "x") or short_flag_present(args, "X")):
        return "git clean -x removes ignored files such as .env; clean build dirs by name instead."
    return None


def _check_gh(seg: list[str]) -> str | None:
    i = command_position(seg, "gh")
    if i is None:
        return None
    args = seg[i + 1 :]
    if args[:2] == ["pr", "merge"] and "--admin" in args:
        return "--admin bypasses branch protection; satisfy the required checks and threads."
    if args[:1] == ["api"]:
        joined = " ".join(args)
        is_mutation = "graphql" in args and "mutation" in joined.lower()
        if is_mutation and not any(m in joined for m in _ALLOWED_MUTATIONS):
            return (
                "GraphQL mutations change repo state without a prompt; only the "
                "review-thread reply/resolve mutations pr-shepherd issues are allowed."
            )
        method = ""
        for j, a in enumerate(args):
            if a in ("-X", "--method") and j + 1 < len(args):
                method = args[j + 1]
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:]
            elif a.startswith("--method="):
                method = a.split("=", 1)[1]
        if method.upper() in ("PUT", "PATCH", "DELETE"):
            return (
                "gh api with a mutating method changes repo state; "
                "repo settings go through scripts/bootstrap-github.sh, run by the conductor."
            )
        has_body = any(a.startswith("-f") or a.startswith("-F") or a == "--input" for a in args)
        if "repos/" in joined and "graphql" not in args and has_body:
            return "gh api POST against a repo endpoint is reserved for the bootstrap script."
    return None


def _check_destructive(seg: list[str], cwd: str) -> str | None:
    i = command_position(seg, "rm")
    if i is not None:
        args = seg[i + 1 :]
        recursive = (
            "--recursive" in args or short_flag_present(args, "r") or short_flag_present(args, "R")
        )
        force = "--force" in args or short_flag_present(args, "f")
        targets = [a for a in args if not a.startswith("-")]
        if recursive and force:
            for t in targets:
                expanded, ok = expand_vars(t, cwd)
                if not ok or not is_scratch_path(expanded, cwd):
                    return (
                        f"recursive+force rm of {t!r} outside build/scratch dirs; "
                        "use git worktree remove / git clean, or ask."
                    )
    i = command_position(seg, "find")
    if i is not None and "-delete" in seg[i + 1 :]:
        roots = [a for a in seg[i + 1 :] if not a.startswith("-")] or ["."]
        for r in roots:
            expanded, ok = expand_vars(r, cwd)
            if not ok or not is_scratch_path(expanded, cwd):
                return "find -delete outside scratch dirs; delete by explicit path instead."
    return None


def _dest_targets(srcs: list[str], dst: str, cwd: str) -> list[str]:
    """For copy-like tools: the destination, plus dest/basename(src) when dest is a directory."""
    expanded, _ = expand_vars(dst, cwd)
    path = Path(expanded) if Path(expanded).is_absolute() else Path(cwd) / expanded
    if dst.endswith("/") or path.is_dir():
        return [dst, *(f"{dst.rstrip('/')}/{Path(s).name}" for s in srcs)]
    return [dst]


def _write_targets(seg: list[str], cwd: str) -> list[str]:
    """Paths a segment writes, deletes, or moves."""
    targets: list[str] = []
    for j, tok in enumerate(seg):
        if tok in (">", ">>") and j + 1 < len(seg):
            targets.append(seg[j + 1])
    for tool in ("tee", "sed", "truncate", "patch", "rm"):
        i = command_position(seg, tool)
        if i is None:
            continue
        args = seg[i + 1 :]
        if tool == "sed" and not any(a.startswith("-i") or a == "--in-place" for a in args):
            continue
        positional = [a for a in args if not a.startswith("-")]
        if tool == "sed" and positional:
            positional = positional[1:]  # first positional is the script
        if tool == "patch":
            positional += [a[2:] for a in args if a.startswith("-i") and len(a) > 2]
        targets.extend(positional)
    for tool in ("cp", "mv", "install", "ln", "rsync"):
        i = command_position(seg, tool)
        if i is None:
            continue
        positional = [a for a in seg[i + 1 :] if not a.startswith("-")]
        if len(positional) >= 2:
            targets.extend(_dest_targets(positional[:-1], positional[-1], cwd))
            if tool == "mv":
                targets.extend(positional[:-1])  # moving a protected file away is a write
        elif positional:
            targets.extend(positional)
    i = command_position(seg, "dd")
    if i is not None:
        targets.extend(a[3:] for a in seg[i + 1 :] if a.startswith("of="))
    i = command_position(seg, "git")
    if i is not None:
        sub, _g, args = git_subcommand(seg[i + 1 :])
        positional = [a for a in args if not a.startswith("-")]
        if sub == "rm" or sub == "restore":
            targets.extend(positional)
        elif sub == "checkout":
            if "--" in args:
                targets.extend(args[args.index("--") + 1 :])
            elif "-b" in args or "-B" in args or "--detach" in args:
                pass
            else:
                # Without `--` git decides ref-vs-path itself; a branch name will
                # never match a protected path, so treat every positional as a path.
                targets.extend(positional)
    return targets


def _cd_target(seg: list[str], cwd: str) -> str | None:
    i = command_position(seg, "cd")
    if i is None:
        return None
    rest = [a for a in seg[i + 1 :] if not a.startswith("-")]
    target = rest[0] if rest else str(Path.home())
    expanded, ok = expand_vars(target, cwd)
    if not ok:
        deny(EVENT, f"cannot resolve the variable in `cd {target}`; use a literal path.")
    return os.path.normpath(expanded if Path(expanded).is_absolute() else str(Path(cwd) / expanded))


def _inspect(command: str, cwd: str, depth: int = 0) -> None:
    if depth > 3:
        deny(EVENT, "command nests shells too deeply to inspect; flatten it.")
    segments = split_segments(command)
    if segments is None:
        deny(EVENT, "command could not be tokenised (unbalanced quote?); rewrite it plainly.")
        return
    for seg in segments:
        for inner in inner_commands(seg):
            _inspect(inner, cwd, depth + 1)
        for reason in (_check_git(seg), _check_gh(seg), _check_destructive(seg, cwd)):
            if reason:
                deny(EVENT, reason)
        for target in _write_targets(seg, cwd):
            if target.startswith("/dev/"):
                continue
            expanded, resolved = expand_vars(target, cwd)
            if not resolved:
                deny(
                    EVENT,
                    f"cannot resolve the variable in write target {target!r}; use a literal path.",
                )
            root, rel = relative_to_checkout(expanded, cwd)
            reason = shell_write_denial(rel, root)
            if reason:
                deny(EVENT, reason)
        new_cwd = _cd_target(seg, cwd)
        if new_cwd:
            cwd = new_cwd


def main() -> None:
    data = read_hook_input()
    if data is None:
        deny(EVENT, "unparseable hook payload; refusing to guess.")
        return
    if data.get("tool_name") != "Bash":
        return
    tool_input = data.get("tool_input")
    command = str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""
    if not command:
        return
    cwd = str(data.get("cwd") or Path.cwd())
    _inspect(command, cwd)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        deny(EVENT, f"guard_bash.py failed internally ({type(exc).__name__}: {exc}); refusing.")
