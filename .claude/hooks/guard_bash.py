#!/usr/bin/env python3
"""PreToolUse(Bash): the hard stops the permission allowlist cannot express.

Blocks, with an explanation the agent can act on:
  * `git commit --no-verify` in any spelling (`-n`, `-nm`, `--no-veri…`);
    `git -c core.hooksPath=…` (hook bypass)
  * force-push in any spelling (`--force`, `-f`, `-fu`, `+refspec`, `--mirror`);
    `--force-with-lease` is allowed
  * any push whose destination is main (`main`, `HEAD:main`, `x:main`,
    `refs/heads/main`, `--all`), deleting main, refspecs containing variables
  * any push of a tag (`--tags`, `--follow-tags`, `refs/tags/…`, a `v<digit>…`
    refspec) and `gh release create|edit|upload|delete|delete-asset`: a public
    release is the owner's action (ADR-0031, M13-11), added 2026-10-15
  * `gh pr merge --admin`; `gh api` with a mutating method (`-X PUT`, `-XPUT`,
    `--method=DELETE`) or a GraphQL mutation other than the two review-thread ones
  * recursive+force `rm`, `find -delete`, `git clean -x` outside scratch dirs
  * `pkill` and `killall`, which match by name machine-wide and kill other
    worktrees' processes (added 2026-09-28 after one did); `kill <pid>` is fine
  * shell writes, deletes and moves touching harness files, ADRs, the backlog
    or LICENSE: redirects (`>`, `>>`, `>|`), `tee`,
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

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

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
_GRAPHQL_REFUSAL = (
    "GraphQL mutations change repo state without a prompt; only the "
    "review-thread reply/resolve mutations pr-shepherd issues are allowed."
)
_NAME = re.compile(r"[_A-Za-z][_0-9A-Za-z]*")
_RELEASE_SUBCOMMANDS = ("create", "edit", "upload", "delete", "delete-asset")
_RELEASE_ONLY_OWNER = (
    "publishing a release (pushing a tag or `gh release …`) is the owner's action "
    "(ADR-0031, M13-11); ask the owner to run it."
)


def _body_query(text: str) -> str:
    """The query a `gh api graphql --input` body sends: its JSON `query`, or,
    when the body is not such JSON, the whole text (judged as a query)."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    query = data.get("query") if isinstance(data, dict) else None
    return query if isinstance(query, str) else text


def _strip_graphql(text: str) -> str:
    """Comments and string literals blanked, so a name inside either is not read
    as a field (M13-71)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "#":
            # GraphQL ends a comment at any line terminator, a lone CR included.
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if text.startswith('"""', i):
            # In a block string `\"""` is an escaped quote, not its end.
            i += 3
            while i < n and not text.startswith('"""', i):
                i += 4 if text.startswith('\\"""', i) else 1
            i += 3
            out.append('""')
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            out.append('""')
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _skip_balanced(text: str, i: int, open_: str, close: str) -> int:
    """The index after the bracket that closes the one at `i`, or -1."""
    depth = 0
    while i < len(text):
        if text[i] == open_:
            depth += 1
        elif text[i] == close:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _mutation_fields(query: str) -> list[str] | None:
    """The top-level field names of every `mutation` operation in `query`, or
    None when a `mutation` keyword is there but its selection cannot be read
    (the caller refuses then). An alias (`a: field`) yields the field."""
    text = _strip_graphql(query)
    fields: list[str] = []

    def skip_ws(k: int) -> int:
        while k < len(text) and (text[k].isspace() or text[k] == ","):
            k += 1
        return k

    def skip_directives(k: int) -> int:
        while k < len(text) and text[k] == "@":
            m = _NAME.match(text, k + 1)
            if not m:
                return -1
            k = skip_ws(m.end())
            if k < len(text) and text[k] == "(":
                k = _skip_balanced(text, k, "(", ")")
                if k < 0:
                    return -1
                k = skip_ws(k)
        return k

    for m in re.finditer(r"(?<![_0-9A-Za-z])mutation(?![_0-9A-Za-z])", text):
        k = skip_ws(m.end())
        name = _NAME.match(text, k)
        if name:
            k = skip_ws(name.end())
        if k < len(text) and text[k] == "(":
            k = _skip_balanced(text, k, "(", ")")
            if k < 0:
                return None
            k = skip_ws(k)
        k = skip_directives(k)
        if k < 0 or k >= len(text) or text[k] != "{":
            return None
        k = skip_ws(k + 1)
        while True:
            if k >= len(text):
                return None
            if text[k] == "}":
                break
            field = _NAME.match(text, k)
            if not field:
                return None
            k = skip_ws(field.end())
            if k < len(text) and text[k] == ":":
                field = _NAME.match(text, skip_ws(k + 1))
                if not field:
                    return None
                k = skip_ws(field.end())
            fields.append(field.group(0))
            if k < len(text) and text[k] == "(":
                k = _skip_balanced(text, k, "(", ")")
                if k < 0:
                    return None
                k = skip_ws(k)
            k = skip_directives(k)
            if k < 0:
                return None
            if k < len(text) and text[k] == "{":
                k = _skip_balanced(text, k, "{", "}")
                if k < 0:
                    return None
                k = skip_ws(k)
    return fields


# gh api's flags that take a value, by short letter and long name, mapped to
# one canonical name. Every other flag is a switch.
_GH_API_SHORT_VALUES = {
    "X": "method",
    "f": "field",
    "F": "field",
    "H": "header",
    "q": "jq",
    "t": "template",
    "p": "preview",
}
_GH_API_LONG_VALUES = {
    "method": "method",
    "field": "field",
    "raw-field": "field",
    "header": "header",
    "input": "input",
    "jq": "jq",
    "template": "template",
    "hostname": "hostname",
    "cache": "cache",
    "preview": "preview",
}


def _parse_gh_api(args: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """The endpoint of `gh api …` and its valued options as (name, value), read
    the way gh's flag parser (pflag) reads them: `--name value`,
    `--name=value`, `-Xvalue`, `-X value` and short clusters such as
    `-iX POST`, where the first value letter takes the rest of the token or
    the next one (M13-71 review)."""
    endpoint = ""
    options: list[tuple[str, str]] = []
    i = 0
    only_args = False
    while i < len(args):
        a = args[i]
        i += 1
        if only_args or not a.startswith("-") or a == "-":
            if not endpoint:
                endpoint = a
            continue
        if a == "--":
            only_args = True
            continue
        if a.startswith("--"):
            name, eq, value = a[2:].partition("=")
            canonical = _GH_API_LONG_VALUES.get(name)
            if canonical is None:
                continue
            if not eq:
                value = args[i] if i < len(args) else ""
                i += 1
            options.append((canonical, value))
            continue
        letters = a[1:]
        for k, letter in enumerate(letters):
            canonical = _GH_API_SHORT_VALUES.get(letter)
            if canonical is None:
                continue
            value = letters[k + 1 :]
            if not value:
                value = args[i] if i < len(args) else ""
                i += 1
            options.append((canonical, value))
            break
    return endpoint, options


def _graphql_refusal(queries: list[str], joined: str) -> str | None:
    """Refuse a `gh api graphql` call that sends any mutation field outside the
    allowlist. The fields are read from the parsed query text, never matched as
    substrings of the command (M13-71). A `mutation` keyword in the command with
    no query text to read is refused too."""
    if not queries:
        return _GRAPHQL_REFUSAL if "mutation" in joined.lower() else None
    for query in queries:
        fields = _mutation_fields(query)
        if fields is None or any(f not in _ALLOWED_MUTATIONS for f in fields):
            return _GRAPHQL_REFUSAL
    return None


def _is_tag_ref(dst: str) -> bool:
    """A push destination that names a tag: refs/tags/… or a release-shaped v<digit>…."""
    return dst.startswith("refs/tags/") or (len(dst) > 1 and dst[0] == "v" and dst[1].isdigit())


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
        if "--tags" in args or "--follow-tags" in args:
            return _RELEASE_ONLY_OWNER
        positional = [a for a in args if not a.startswith("-")]
        if ("--delete" in args or short_flag_present(args, "d")) and "main" in positional:
            return "refusing to delete main."
        for spec in positional:
            if "$" in spec:
                return "variables in a push refspec cannot be checked; use literal refspecs."
            dst, force = _refspec_dst(spec)
            if _is_tag_ref(dst) or _is_tag_ref(spec.lstrip("+").split(":", 1)[0]):
                return _RELEASE_ONLY_OWNER
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


def _check_gh(seg: list[str], cwd: str = "", written: frozenset[str] = frozenset()) -> str | None:
    i = command_position(seg, "gh")
    if i is None:
        return None
    args = seg[i + 1 :]
    if args[:1] == ["release"] and len(args) > 1 and args[1] in _RELEASE_SUBCOMMANDS:
        return _RELEASE_ONLY_OWNER
    if args[:2] == ["pr", "merge"] and "--admin" in args:
        return "--admin bypasses branch protection; satisfy the required checks and threads."
    if args[:1] == ["api"]:
        joined = " ".join(args)
        # Every query text the call can send is collected and judged: an inline
        # `-f query=...`, a `query=@<file>` (2026-10-19, M14-02 notes) and a JSON
        # body given with `--input <file>` (M13-71). A file that cannot be read
        # now is refused: write it first, then run the call.
        # The GraphQL endpoint in any spelling gh accepts: `graphql`, `/graphql`
        # or a full URL ending in `/graphql` (M13-71 review).
        endpoint, options = _parse_gh_api(args[1:])
        has_body = any(name in ("field", "input") for name, _ in options)
        # Fail closed (M13-71 review): any endpoint spelling that names graphql,
        # whatever its case, fragment, parameters or percent-encoding, is judged
        # as GraphQL; an endpoint the shell has yet to expand is refused when
        # the call sends anything.
        if any(ch in endpoint for ch in "$`") and (has_body or "mutation" in joined.lower()):
            return (
                "gh api with an endpoint the shell expands cannot be checked; "
                "write the endpoint literally."
            )
        plain = unquote(endpoint).lower()
        is_graphql = "graphql" in plain
        queries: list[str] = []
        for name, value in options if is_graphql else ():
            spec = value if name == "field" else ""
            body = value if name == "input" else ""
            if spec.startswith("query=") and not spec.startswith("query=@"):
                queries.append(spec[len("query=") :])
                continue
            raw = spec[len("query=@") :] if spec.startswith("query=@") else body
            if not raw:
                continue
            if raw == "-":
                return (
                    "gh api reads that body from stdin, which this guard cannot check; "
                    "write it to a file first and pass the file."
                )
            expanded, ok = expand_vars(raw, cwd) if cwd else (raw, True)
            path = Path(expanded)
            if cwd and not path.is_absolute():
                path = Path(cwd) / path
            if not ok or os.path.normpath(str(path)) in written:
                return (
                    "gh api graphql with a query file is checked against the file as it is "
                    "now; write the file in its own command, with a literal path, then run "
                    "the call."
                )
            try:
                # newline="": judge the bytes gh sends, a lone CR included.
                with path.open(encoding="utf-8", newline="") as handle:
                    text = handle.read()
            except (OSError, UnicodeDecodeError):
                return (
                    "gh api graphql reads a query file to check it for mutations; "
                    "write the file first, then run the call."
                )
            if body:
                queries.append(_body_query(text))
            else:
                queries.append(text)
        if is_graphql:
            refusal = _graphql_refusal(queries, joined)
            if refusal:
                return refusal
        methods = [value for name, value in options if name == "method"]
        if any(m.upper() in ("PUT", "PATCH", "DELETE") for m in methods):
            return (
                "gh api with a mutating method changes repo state; "
                "repo settings go through scripts/bootstrap-github.sh, run by the conductor."
            )
        # A body sent to a repo endpoint is refused whether or not the endpoint
        # also names graphql: a real GraphQL endpoint never holds `repos/`
        # (M13-71 review).
        if ("repos/" in plain or (not is_graphql and "repos/" in joined)) and has_body:
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
    for tool in ("pkill", "killall"):
        if command_position(seg, tool) is not None:
            return (
                f"{tool} matches processes by name across the whole machine, so it kills "
                "other worktrees' test runs too; stop your own process with `kill <pid>` "
                "(from the pid you started) or let it finish."
            )
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


def _inspect(
    command: str,
    cwd: str,
    agent_type: str | None = None,
    depth: int = 0,
    outer_written: frozenset[str] = frozenset(),
) -> None:
    if depth > 3:
        deny(EVENT, "command nests shells too deeply to inspect; flatten it.")
    segments = split_segments(command)
    if segments is None:
        deny(EVENT, "command could not be tokenised (unbalanced quote?); rewrite it plainly.")
        return
    # Every path this command line writes, with the cd chain followed, so a
    # gh api graphql query file written earlier in the same line is refused:
    # the hook reads the file before the shell runs (2026-10-19, M14-02 notes).
    written: set[str] = set(outer_written)
    scan_cwd = cwd
    for seg in segments:
        for target in _write_targets(seg, scan_cwd):
            expanded, ok = expand_vars(target, scan_cwd)
            if ok:
                full = expanded if Path(expanded).is_absolute() else str(Path(scan_cwd) / expanded)
                written.add(os.path.normpath(full))
        scan_cwd = _cd_target(seg, scan_cwd) or scan_cwd
    for seg in segments:
        for inner in inner_commands(seg):
            _inspect(inner, cwd, agent_type, depth + 1, frozenset(written))
        for reason in (
            _check_git(seg),
            _check_gh(seg, cwd, frozenset(written)),
            _check_destructive(seg, cwd),
        ):
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
            _root, rel = relative_to_checkout(expanded, cwd)
            reason = shell_write_denial(rel, agent_type)
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
    agent_type = data.get("agent_type") or None
    _inspect(command, cwd, str(agent_type) if agent_type else None)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        deny(EVENT, f"guard_bash.py failed internally ({type(exc).__name__}: {exc}); refusing.")
