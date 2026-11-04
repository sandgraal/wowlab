"""The UI parity test (M13-26, ADR-0029 and its 2026-10-15 amendment).

`wowlab ui` builds its commands from the CLI's Click tree at start, so a new
command reaches the UI with no UI code. This test keeps that true. It runs in
the required `quality` job, with the rest of `lab/core/tests`.

- Every leaf of the real Click tree is reachable in the UI: listed by `GET
  /api/commands`, not excluded, declaring `--json`, with every parameter it
  declares offered to the form in the shape Click gives it, and a run through
  the server reaching each parameter when Click parses the argv back.
  Otherwise it sits in an exclusion list below, with a written reason.
- The exclusion lists are checked both ways: every entry names a real leaf or
  a declared, offered parameter, and the server gives the same reason.
- Every leaf that declares `--yes` goes through the confirm step (ADR-0029,
  "Parity"; M14-02, M14-03): it is in `CONFIRM_STEP`, which is read from the
  Click tree here (each `--yes` leaf with the plan-run shape its
  declarations give it, §16.1), never listed or counted, and the server runs
  it only as a plan, never with `--yes` from a form. On the real tree no
  `--yes` leaf is excluded; a `--yes` leaf with no shape (a dummy) is
  excluded with `NO_CONFIRM_SHAPE`, and one the server is made to run
  without the step fails this check.
- `ui` itself is excluded.
- `looks page --out` and `log tail --follow` are refused.
- A dummy command added to the tree in the test appears in the UI, runs, and
  needs no UI code. Dummies the form cannot honestly offer fail parity, so
  each check above is shown to be able to fail.
- The page draws text only: no HTML or code sink in `page.js`.

Nothing here reads or writes an install. The `wowlab` the server starts is a
constructed stand-in (`_ECHO`) that prints its argv as JSON, so a run proves
what the server would have run without running it.

Owner of this file: `test-writer` (audit of 2026-10-16; M13-26 was exempted
from graders-first on that date). Since the owner decision of 2026-10-17
(AGENTS.md) `wowlab_core.ui` is out of the graders-first set and its tests
are the implementer's; M14-02 added the confirm-step check and
`CONFIRM_STEP`, and M14-03 derived it from the tree and gave the six install
writes their shapes.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import re
import sys
import threading
from collections.abc import Iterator, Mapping
from importlib import resources
from typing import Annotated, Any

import pytest
import typer
from typer._click import Context

from wowlab_core import ui

# ─── the exclusion lists, each with its written reason ───────────────────────

NO_CONFIRM_SHAPE = "--yes with no plan run the UI can confirm"
"""The reason for a leaf that declares `--yes` and has no plan-run shape
(M14-03; it replaced "confirm flow: M14", which no leaf of the real tree
holds any more). Those leaves are found in the tree, never listed by hand."""

# The eight `--yes` leaves of §16.1 on 2026-10-18, by name only to prove the
# derived set still holds them (tests may name commands; the server does not).
_KNOWN_YES = {
    ("snap", "gc"),
    ("doctor",),
    ("snap", "restore"),
    ("undo",),
    ("profile", "apply"),
    ("sv", "merge"),
    ("addon", "install"),
    ("addon", "remove"),
}

EXCLUDED: Mapping[tuple[str, ...], str] = {
    # The server must not start another server, or itself.
    ("ui",): "the UI itself",
}
"""Leaves excluded for a reason other than `--yes`. A new entry needs its
reason written here and in the server, and nothing else."""

REFUSED_PARAMS: Mapping[tuple[str, ...], Mapping[str, str]] = {
    # Owner rulings of 2026-10-15 (ADR-0029).
    ("log", "tail"): {"follow": "a run must end"},
    ("looks", "page"): {"out": "it can create a file anywhere outside an install"},
}
"""Parameters the UI refuses on a leaf that otherwise runs."""

FORCED_FLAGS: Mapping[tuple[str, ...], Mapping[str, str]] = {
    # M16-42: from the UI, `sv dump` reads only a file in the install.
    ("sv", "dump"): {"install_only": "--install-only"},
}
"""Flags the server gives every run of a leaf, so not ones the form offers."""

# The stand-in `wowlab`: prints the argv it was given, as JSON (constructed).
_ECHO = "import json, sys; print(json.dumps(sys.argv[1:]))"

# The Click parameter the server adds itself, so not one the form offers.
_JSON_OPT = "--json"

# A value that looks like an option, so a run proves it arrives as a value
# and never as the flag it spells (constructed).
_HOSTILE = "--yes"


# ─── the Click tree, read independently of the server ────────────────────────


def _leaves(cmd: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if hasattr(cmd, "list_commands"):
        ctx = Context(cmd)
        for name in cmd.list_commands(ctx):
            sub = cmd.get_command(ctx, name)
            if sub is not None:
                yield from _leaves(sub, (*path, str(name)))
    else:
        yield path, cmd


def _opts(param: Any) -> list[str]:
    return [*getattr(param, "opts", ()), *getattr(param, "secondary_opts", ())]


def _declares_yes(cmd: Any) -> bool:
    return any("--yes" in _opts(param) for param in cmd.params)


def _declares_json(cmd: Any) -> bool:
    return any(_JSON_OPT in _opts(param) for param in cmd.params)


def _declared_params(cmd: Any) -> dict[str, Any]:
    """The parameters a leaf declares, by name, `--json` aside."""
    return {str(p.name): p for p in cmd.params if _JSON_OPT not in _opts(p)}


def _declared(cmd: Any) -> set[str]:
    return set(_declared_params(cmd))


def _declares_dry_run(cmd: Any) -> bool:
    return any("--dry-run" in _opts(param) for param in cmd.params)


def _declares_expect(cmd: Any) -> bool:
    return any("--expect" in _opts(param) for param in cmd.params)


def _shape_in_tree(path: tuple[str, ...], cmd: Any) -> str | None:
    """The plan-run shape §16.1 gives a `--yes` leaf, from what it declares:
    "dry_run" (it declares `--dry-run`), "doctor", or "declined" (no
    `--dry-run`, it confirms through `cli._confirm` and declares `--expect`,
    whose report field the CLI exports); None for any other."""
    from wowlab_core import cli

    if _declares_dry_run(cmd):
        return "dry_run"
    if path == ("doctor",):
        return "doctor"
    if _declares_expect(cmd) and path in cli.EXPECT_FIELDS:
        return "declined"
    return None


def _confirm_step(tree: Any) -> dict[tuple[str, ...], str]:
    """The `--yes` leaves of `tree` that have a plan-run shape, with it."""
    step: dict[tuple[str, ...], str] = {}
    for path, cmd in _leaves(tree):
        if _declares_yes(cmd) and path != ("ui",):
            shape = _shape_in_tree(path, cmd)
            if shape is not None:
                step[path] = shape
    return step


def _server_added(cmd: Any, shape: str | None) -> dict[str, Any]:
    """The parameters the server sets itself on a leaf of the confirm step,
    so not ones the form offers: `--yes`, `--dry-run` for the dry-run shape
    and `--expect` for the declined one. Empty for any other leaf."""
    if shape is None:
        return {}
    added = {}
    for param in cmd.params:
        opts = _opts(param)
        if (
            "--yes" in opts
            or (shape == "dry_run" and "--dry-run" in opts)
            or (shape == "declined" and "--expect" in opts)
        ):
            added[str(param.name)] = param
    return added


def _click_shape(param: Any) -> tuple[str, bool, bool]:
    """(kind, multiple, required) as Click declares the parameter."""
    if getattr(param, "param_type_name", "") == "argument":
        return "argument", getattr(param, "nargs", 1) == -1, bool(param.required)
    if getattr(param, "is_flag", False):
        return "flag", False, bool(param.required)
    return "option", bool(getattr(param, "multiple", False)), bool(param.required)


def _real_tree() -> Any:
    from wowlab_core import cli

    return typer.main.get_command(cli.app)


CONFIRM_STEP: Mapping[tuple[str, ...], str] = _confirm_step(_real_tree())
"""The `--yes` leaves of the real tree that go through the confirm step,
each with the shape of its plan run, read from the tree at test time:
"dry_run" (`--dry-run --json`), "doctor" (`--json` alone) or "declined"
(`--json` alone, ended by `_confirm`'s line; `undo` and `sv merge`)."""


_DummyLevel = Annotated[int, typer.Option("--level", help="How far down to look.")]
_DummyJson = Annotated[bool, typer.Option("--json")]


def _tree_with_dummies() -> Any:
    """The real tree with two commands no UI code has ever seen (constructed):
    a read command, and one that declares `--yes`."""
    extra = typer.Typer()

    @extra.command("parity-dummy")
    def parity_dummy(
        target: Annotated[str, typer.Argument(help="What to look at.")],
        level: _DummyLevel = 1,
        verbose: Annotated[bool, typer.Option("--verbose")] = False,
        tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
        json_out: _DummyJson = False,
    ) -> None:
        """A dummy read command added by the parity test."""

    @extra.command("parity-dummy-writes")
    def parity_dummy_writes(
        yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
        json_out: _DummyJson = False,
    ) -> None:
        """A dummy command that asks before changing files."""

    tree = _real_tree()
    for path, cmd in _leaves(typer.main.get_command(extra)):
        tree.add_command(cmd, path[-1])
    return tree


def _tree_with_bad_dummies() -> Any:
    """The real tree with commands the form cannot honestly offer
    (constructed). Each must fail parity; see `_BAD_CASES`."""
    extra = typer.Typer()

    @extra.command("parity-bad-default-on")
    def default_on(
        color: Annotated[bool, typer.Option("--color")] = True,
        json_out: _DummyJson = False,
    ) -> None:
        """A flag that is on unless switched off, with no way to switch it off."""

    @extra.command("parity-bad-on-off")
    def on_off(
        auto: bool = False,
        json_out: _DummyJson = False,
    ) -> None:
        """A `--auto/--no-auto` flag."""

    @extra.command("parity-bad-no-json")
    def no_json(target: Annotated[str, typer.Argument()] = "x") -> None:
        """A read command with no `--json`."""

    tree = _real_tree()
    for path, cmd in _leaves(typer.main.get_command(extra)):
        tree.add_command(cmd, path[-1])
    return tree


# ─── the server ──────────────────────────────────────────────────────────────


class _Served:
    def __init__(self, tree: Any) -> None:
        self.server = ui.make_server(
            command=tree, wowlab_argv=[sys.executable, "-c", _ECHO], timeout_s=60
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)

    def request(self, method: str, path: str, body: object = None) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=60)
        try:
            data = None if body is None else json.dumps(body).encode("utf-8")
            headers = {} if data is None else {"Content-Type": "application/json"}
            conn.request(method, f"{path}?token={self.server.token}", body=data, headers=headers)
            reply = conn.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            conn.close()

    def commands(self) -> dict[tuple[str, ...], dict[str, Any]]:
        status, payload = self.request("GET", "/api/commands")
        assert status == 200, payload
        listed = {tuple(item["path"]): item for item in payload["commands"]}
        assert len(listed) == len(payload["commands"]), "a command is listed twice"
        return listed

    def run(self, path: tuple[str, ...], params: Mapping[str, object]) -> tuple[int, Any]:
        return self.request("POST", "/api/run", {"command": list(path), "params": dict(params)})


@pytest.fixture(scope="module")
def real() -> Iterator[_Served]:
    served = _Served(_real_tree())
    yield served
    served.close()


@pytest.fixture(scope="module")
def dummied() -> Iterator[_Served]:
    served = _Served(_tree_with_dummies())
    yield served
    served.close()


@pytest.fixture(scope="module")
def bad() -> Iterator[_Served]:
    served = _Served(_tree_with_bad_dummies())
    yield served
    served.close()


def _expected_exclusions(
    tree: Any,
    excluded: Mapping[tuple[str, ...], str] = EXCLUDED,
    confirm_step: Mapping[tuple[str, ...], str] = CONFIRM_STEP,
) -> dict[tuple[str, ...], str]:
    expected = {
        path: NO_CONFIRM_SHAPE
        for path, cmd in _leaves(tree)
        if _declares_yes(cmd) and path not in confirm_step
    }
    expected.update(excluded)
    return expected


def _list_problems(
    tree: Any,
    served: _Served,
    excluded: Mapping[tuple[str, ...], str],
    refused: Mapping[tuple[str, ...], Mapping[str, str]],
) -> list[str]:
    """The exclusion lists, checked from their side: no stale or vague entry."""
    leaves = dict(_leaves(tree))
    listed = served.commands()
    problems: list[str] = []
    for path, reason in excluded.items():
        name = " ".join(path)
        if not reason.strip():
            problems.append(f"EXCLUDED {name}: no written reason")
        if path not in leaves:
            problems.append(f"EXCLUDED {name}: not a leaf of the tree")
        elif _declares_yes(leaves[path]):
            problems.append(
                f"EXCLUDED {name}: declares --yes, so its reason is {NO_CONFIRM_SHAPE!r}"
            )
    every_exclusion = _expected_exclusions(tree, excluded)
    for path, names in refused.items():
        leaf = " ".join(path)
        if path not in leaves:
            problems.append(f"REFUSED_PARAMS {leaf}: not a leaf of the tree")
            continue
        if path in every_exclusion:
            problems.append(f"REFUSED_PARAMS {leaf}: the leaf is excluded, nothing to refuse")
            continue
        declared = _declared(leaves[path])
        offered = {p["name"]: p for p in listed[path]["params"]} if path in listed else {}
        for name, reason in names.items():
            if not reason.strip():
                problems.append(f"REFUSED_PARAMS {leaf} {name}: no written reason")
            if name not in declared:
                problems.append(f"REFUSED_PARAMS {leaf} {name}: not a declared parameter")
            elif name not in offered:
                problems.append(f"REFUSED_PARAMS {leaf} {name}: declared but not offered")
            elif offered[name]["refused"] != reason:
                problems.append(
                    f"REFUSED_PARAMS {leaf} {name}: server reason "
                    f"{offered[name]['refused']!r}, want {reason!r}"
                )
    return problems


def _parity_problems(
    tree: Any,
    served: _Served,
    excluded: Mapping[tuple[str, ...], str] = EXCLUDED,
    refused: Mapping[tuple[str, ...], Mapping[str, str]] = REFUSED_PARAMS,
    confirm_step: Mapping[tuple[str, ...], str] = CONFIRM_STEP,
) -> list[str]:
    """Every leaf of `tree` is reachable in the UI or excluded with its reason,
    every `--yes` leaf goes through the confirm step, and every list entry is
    real. Returns what is wrong, empty when nothing."""
    leaves = dict(_leaves(tree))
    listed = served.commands()
    problems: list[str] = []
    for path in sorted(set(leaves) - set(listed)):
        problems.append(f"{' '.join(path)}: a leaf the UI does not list")
    for path in sorted(set(listed) - set(leaves)):
        problems.append(f"{' '.join(path)}: listed but not a leaf")
    problems += _list_problems(tree, served, excluded, refused)
    problems += _confirm_problems(tree, served, confirm_step)
    want_excluded = _expected_exclusions(tree, excluded, confirm_step)
    for path, cmd in leaves.items():
        if path not in listed:
            continue
        item = listed[path]
        name = " ".join(path)
        if path in want_excluded:
            if item["excluded"] != want_excluded[path]:
                problems.append(
                    f"{name}: excluded {item['excluded']!r}, want {want_excluded[path]!r}"
                )
            continue
        if item["excluded"] is not None:
            problems.append(f"{name}: excluded {item['excluded']!r} with no written reason here")
            continue
        if not _declares_json(cmd):
            problems.append(f"{name}: declares no --json, so the UI has no JSON to show")
        declared = _declared_params(cmd)
        for added in _server_added(cmd, confirm_step.get(path)):
            declared.pop(added)
        for forced in FORCED_FLAGS.get(path, {}):
            declared.pop(forced)
        offered = {p["name"]: p for p in item["params"]}
        for missing in sorted(set(declared) - set(offered)):
            problems.append(f"{name}: declares {missing!r} but the form cannot offer it")
        for extra in sorted(set(offered) - set(declared)):
            problems.append(f"{name}: offers {extra!r}, which it does not declare")
        for pname, param in offered.items():
            if pname not in declared:
                continue
            click_param = declared[pname]
            kind, multiple, required = _click_shape(click_param)
            got = (param["kind"], param["multiple"], param["required"])
            if got != (kind, multiple, required):
                problems.append(
                    f"{name}: {pname} offered as (kind, multiple, required) {got}, "
                    f"Click declares {(kind, multiple, required)}"
                )
            if kind != "argument" and param["label"] not in _opts(click_param):
                problems.append(f"{name}: {pname} labelled {param['label']!r}, not one of its opts")
            if kind == "flag" and click_param.default:
                # The page draws a flag as an unchecked box that can only add
                # the flag; a flag that is on by default could never go off.
                problems.append(
                    f"{name}: {pname} is a flag that defaults to on, and the form "
                    "cannot switch it off"
                )
            want = refused.get(path, {}).get(pname)
            if param["refused"] != want:
                problems.append(f"{name}: {pname} refused {param['refused']!r}, want {want!r}")
            if param["required"] and param["refused"]:
                problems.append(f"{name}: {pname} is required and refused, so unreachable")
    return problems


def _confirm_problems(
    tree: Any, served: _Served, confirm_step: Mapping[tuple[str, ...], str]
) -> list[str]:
    """ADR-0029 "Parity": every `--yes` command goes through the confirm
    step. A leaf of `confirm_step` is listed with its shape and without its
    `--yes` (or `--dry-run`); a run of it is its plan, which never carries
    `--yes` and carries `--dry-run` exactly for the dry-run shape; a request
    that sets `yes`, or brings a token the server never minted, runs
    nothing. Every other `--yes` leaf is excluded and refuses to run. No
    leaf without `--yes` claims the step."""
    leaves = dict(_leaves(tree))
    listed = served.commands()
    problems: list[str] = []
    for path in sorted(confirm_step):
        if path not in leaves or not _declares_yes(leaves[path]):
            problems.append(f"CONFIRM_STEP {' '.join(path)}: not a leaf that declares --yes")
    for path, cmd in leaves.items():
        if path not in listed:
            continue
        item = listed[path]
        name = " ".join(path)
        if not _declares_yes(cmd):
            if item.get("confirm") is not None:
                problems.append(f"{name}: claims the confirm step and declares no --yes")
            continue
        shape = confirm_step.get(path)
        if shape is None:
            if item["excluded"] is None:
                problems.append(f"{name}: declares --yes and skips the confirm step")
            if item.get("confirm") is not None:
                problems.append(f"{name}: in the confirm step, but not in CONFIRM_STEP")
        else:
            if item["excluded"] is not None or item.get("confirm") != shape:
                problems.append(
                    f"{name}: excluded {item['excluded']!r}, confirm {item.get('confirm')!r}; "
                    f"want the confirm step, shape {shape!r}"
                )
            if _shape_in_tree(path, cmd) != shape:
                problems.append(f"{name}: the tree gives shape {_shape_in_tree(path, cmd)!r}")
        offered = {p["name"] for p in item["params"]}
        for added in _server_added(cmd, shape):
            if added in offered:
                problems.append(f"{name}: offers {added!r}, which only the server sets")
        # A request that sets `--yes` itself never runs.
        yes_names = [str(p.name) for p in cmd.params if "--yes" in _opts(p)]
        for yes_name in yes_names:
            status, payload = served.run(path, {yes_name: True})
            if status == 200:
                problems.append(f"{name}: a request that sets {yes_name!r} ran: {payload}")
        # Nor does a token the server never minted.
        status, _ = served.request(
            "POST", "/api/run", {"command": list(path), "params": {}, "confirm": "x" * 43}
        )
        if status == 200:
            problems.append(f"{name}: a made-up confirm token ran")
        status, payload = served.run(path, {})
        if shape is None:
            if status == 200:
                problems.append(f"{name}: ran without the confirm step")
            continue
        if status != 200:
            problems.append(f"{name}: its plan run answered {status} {payload}")
            continue
        offer = payload.get("confirm")
        if not isinstance(offer, dict) or offer.get("stage") != "plan":
            problems.append(f"{name}: its run is not reported as a plan: {offer!r}")
        argv = json.loads(payload["stdout"])
        parsed, _, _ = cmd.make_parser(Context(cmd)).parse_args(argv[len(path) :])
        for yes_name in yes_names:
            if parsed.get(yes_name):
                problems.append(f"{name}: its plan run carries --yes: {argv}")
        if _JSON_OPT not in argv:
            problems.append(f"{name}: its plan run carries no --json: {argv}")
        if ("--dry-run" in argv) != (shape == "dry_run"):
            problems.append(f"{name}: --dry-run in its plan run is not its shape's: {argv}")
        if any(a == "--expect" or a.startswith("--expect=") for a in argv):
            problems.append(f"{name}: its plan run carries --expect: {argv}")
        # A form cannot set `--expect` on a leaf of the declined shape.
        if shape == "declined":
            for param in cmd.params:
                if "--expect" in _opts(param):
                    status, payload = served.run(path, {str(param.name): "x"})
                    if status == 200:
                        problems.append(f"{name}: a request that sets --expect ran: {payload}")
    return problems


def _assert_parity(tree: Any, served: _Served) -> None:
    assert _parity_problems(tree, served) == []


# ─── the parity tests ────────────────────────────────────────────────────────


def test_every_leaf_is_reachable_or_excluded_with_a_written_reason(real: _Served) -> None:
    _assert_parity(_real_tree(), real)
    for path, reason in EXCLUDED.items():
        assert reason.strip(), path
    for path, refused in REFUSED_PARAMS.items():
        for name, reason in refused.items():
            assert reason.strip(), (path, name)


def test_every_excluded_entry_is_a_real_leaf_without_yes(real: _Served) -> None:
    leaves = dict(_leaves(_real_tree()))
    listed = real.commands()
    for path, reason in EXCLUDED.items():
        assert path in leaves, f"EXCLUDED names {path}, which is not a leaf"
        assert not _declares_yes(leaves[path]), path
        assert listed[path]["excluded"] == reason, path


def test_every_refused_param_is_declared_and_offered_with_the_servers_reason(
    real: _Served,
) -> None:
    tree = _real_tree()
    leaves = dict(_leaves(tree))
    listed = real.commands()
    excluded = _expected_exclusions(tree)
    for path, names in REFUSED_PARAMS.items():
        assert path in leaves, f"REFUSED_PARAMS names {path}, which is not a leaf"
        assert path not in excluded, f"{path} is excluded, so refusing a parameter is moot"
        assert listed[path]["excluded"] is None, path
        offered = {p["name"]: p for p in listed[path]["params"]}
        for name, reason in names.items():
            assert name in _declared(leaves[path]), (path, name)
            assert name in offered, (path, name)
            assert offered[name]["refused"] == reason, (path, name)
    # And from the server's side: nothing refused that the list does not name.
    for path, item in listed.items():
        for param in item["params"]:
            if param["refused"] is not None:
                assert REFUSED_PARAMS.get(path, {}).get(param["name"]) == param["refused"], (
                    path,
                    param["name"],
                )


def test_every_yes_leaf_goes_through_the_confirm_step(real: _Served) -> None:
    """ADR-0029 "Parity" (M14-02, M14-03): every `--yes` leaf of the real
    tree, read from the tree, goes through the confirm step with the shape
    its declarations give it; none is excluded, and `ui` alone is."""
    tree = _real_tree()
    yes = {path for path, cmd in _leaves(tree) if _declares_yes(cmd)}
    assert yes >= _KNOWN_YES
    assert set(CONFIRM_STEP) == yes, "a --yes leaf has no plan-run shape"
    assert {CONFIRM_STEP[p] for p in [("undo",), ("sv", "merge")]} == {"declined"}
    assert CONFIRM_STEP[("doctor",)] == "doctor"
    assert {p for p, shape in CONFIRM_STEP.items() if shape == "dry_run"} >= _KNOWN_YES - {
        ("undo",),
        ("sv", "merge"),
        ("doctor",),
    }
    assert _confirm_problems(tree, real, CONFIRM_STEP) == []
    listed = real.commands()
    assert {path for path, item in listed.items() if item["excluded"] is not None} == {("ui",)}
    assert not any(item["excluded"] == NO_CONFIRM_SHAPE for item in listed.values())
    assert {path for path, item in listed.items() if item["confirm"]} == set(CONFIRM_STEP)
    for path in sorted(CONFIRM_STEP):
        status, payload = real.run(path, {"yes": True})
        assert (status, payload) == (403, {"error": "unknown_parameter"}), path


def _skipping(tree: Any, path: tuple[str, ...], **change: Any) -> _Served:
    """A server for `tree` whose leaf at `path` is altered as a broken server
    would hold it (constructed): the parity check must catch it."""
    served = _Served(tree)
    server = served.server
    server.leaves[path] = dataclasses.replace(server.leaves[path], **change)
    server.commands_body = ui._commands_body(server.leaves)
    return served


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"excluded": None}, "parity-dummy-writes: declares --yes and skips the confirm step"),
        ({"excluded": None}, "parity-dummy-writes: a request that sets 'yes' ran"),
        ({"excluded": None}, "parity-dummy-writes: ran without the confirm step"),
        (
            {"excluded": None, "confirm": "dry_run"},
            "parity-dummy-writes: in the confirm step, but not in CONFIRM_STEP",
        ),
    ],
    ids=[
        "constructed: a --yes leaf run as a plain leaf",
        "constructed: its --yes reachable from a form",
        "constructed: it runs with no plan",
        "constructed: a leaf the step does not name",
    ],
)
def test_the_confirm_step_check_fails_when_a_yes_leaf_skips_it(
    change: dict[str, Any], fragment: str
) -> None:
    tree = _tree_with_dummies()
    path = ("parity-dummy-writes",)
    served = _skipping(tree, path, **change)
    try:
        problems = _parity_problems(tree, served)
    finally:
        served.close()
    assert any(fragment in p for p in problems), problems


def test_the_confirm_step_check_fails_when_a_named_leaf_is_excluded() -> None:
    """CONFIRM_STEP naming a leaf the server still excludes is caught too
    (constructed: the list widened by the test)."""
    tree = _tree_with_dummies()
    served = _Served(tree)
    try:
        step = {**CONFIRM_STEP, ("parity-dummy-writes",): "doctor"}
        problems = _parity_problems(tree, served, confirm_step=step)
    finally:
        served.close()
    assert any(
        p.startswith(f"parity-dummy-writes: excluded {NO_CONFIRM_SHAPE!r}") for p in problems
    ), problems


def test_the_reason_confirm_flow_m14_has_left_the_code() -> None:
    """M14-03: the reason string of the M14 exclusion is gone from the server."""
    source = (resources.files(ui) / "__init__.py").read_text(encoding="utf-8")
    assert "confirm flow: M14" not in source
    assert "_CONFIRM_READY" not in source


def test_ui_itself_is_excluded(real: _Served) -> None:
    assert real.commands()[("ui",)]["excluded"] == EXCLUDED[("ui",)]
    assert real.run(("ui",), {}) == (403, {"error": "excluded"})


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (("looks", "page"), {"out": "page.html"}),
        (("looks", "page"), {"out": ""}),
        (("log", "tail"), {"follow": True}),
    ],
    ids=["looks page --out", "looks page --out empty", "log tail --follow"],
)
def test_looks_page_out_and_log_tail_follow_are_refused(
    real: _Served, path: tuple[str, ...], params: dict[str, object]
) -> None:
    item = real.commands()[path]
    (name,) = params
    offered = {p["name"]: p for p in item["params"]}
    assert offered[name]["refused"] == REFUSED_PARAMS[path][name]
    assert real.run(path, params) == (403, {"error": "excluded"})


def test_log_tail_without_follow_still_runs(real: _Served) -> None:
    status, payload = real.run(("log", "tail"), {"follow": False})
    assert status == 200, payload
    argv = json.loads(payload["stdout"])
    assert argv[:2] == ["log", "tail"]
    assert not any(a.startswith("--follow") for a in argv), argv


def _sample(param: dict[str, Any]) -> tuple[object, object]:
    """(what the form sends, what Click's parser must route to the parameter)."""
    if param["kind"] == "flag":
        return True, True
    if param["multiple"]:
        values = [_HOSTILE, "two words"]
        return values, values
    return _HOSTILE, _HOSTILE


def _reachable(real_tree: Any) -> list[tuple[str, ...]]:
    excluded = _expected_exclusions(real_tree)
    return [path for path, _ in _leaves(real_tree) if path not in excluded]


@pytest.mark.parametrize("path", _reachable(_real_tree()), ids=lambda p: " ".join(p))
def test_a_run_reaches_every_offered_parameter_when_click_parses_it_back(
    real: _Served, path: tuple[str, ...]
) -> None:
    """Sent through the server with a value that looks like an option, every
    offered parameter lands on itself when Click parses the argv, and
    nothing lands anywhere else, `--yes` least of all."""
    item = real.commands()[path]
    sent: dict[str, object] = {}
    want: dict[str, object] = {}
    for param in item["params"]:
        if param["refused"] is not None:
            continue
        sent[param["name"]], want[param["name"]] = _sample(param)
    status, payload = real.run(path, sent)
    assert status == 200, payload
    assert payload["exit_code"] == 0, payload
    argv = json.loads(payload["stdout"])
    assert tuple(argv[: len(path)]) == path, argv

    cmd = dict(_leaves(_real_tree()))[path]
    parsed, leftover, _ = cmd.make_parser(Context(cmd)).parse_args(argv[len(path) :])
    assert leftover == [], argv
    json_names = {str(p.name) for p in cmd.params if _JSON_OPT in _opts(p)}
    got = {k: (list(v) if isinstance(v, tuple) else v) for k, v in parsed.items()}
    # A leaf of the confirm step runs its plan: the server adds `--dry-run`
    # for the dry-run shape, and never `--yes`.
    if CONFIRM_STEP.get(path) == "dry_run":
        want.update({str(p.name): True for p in cmd.params if "--dry-run" in _opts(p)})
    # A flag the server always gives (M16-42) is on in every run.
    want.update(dict.fromkeys(FORCED_FLAGS.get(path, {}), True))
    assert {k: v for k, v in got.items() if k not in json_names} == want, argv
    assert [got.get(n) for n in json_names] == [True] * len(json_names), argv


def test_a_dummy_command_appears_in_the_ui_with_no_ui_code(dummied: _Served) -> None:
    tree = _tree_with_dummies()
    _assert_parity(tree, dummied)
    listed = dummied.commands()

    item = listed[("parity-dummy",)]
    assert item["excluded"] is None
    assert item["help"] == "A dummy read command added by the parity test."
    params = {p["name"]: p for p in item["params"]}
    assert set(params) == {"target", "level", "verbose", "tag"}
    assert (params["target"]["kind"], params["target"]["required"]) == ("argument", True)
    assert (params["level"]["kind"], params["level"]["label"]) == ("option", "--level")
    assert params["level"]["help"] == "How far down to look."
    assert params["verbose"]["kind"] == "flag"
    assert (params["tag"]["kind"], params["tag"]["multiple"]) == ("option", True)

    status, payload = dummied.run(
        ("parity-dummy",), {"target": "x", "level": "3", "verbose": True, "tag": ["a", "b"]}
    )
    assert status == 200, payload
    assert payload["exit_code"] == 0, payload
    argv = json.loads(payload["stdout"])
    assert argv[0] == "parity-dummy"
    assert sorted(argv[1:]) == sorted(
        ["--level=3", "--verbose", "--tag=a", "--tag=b", "--json", "--", "x"]
    )

    assert listed[("parity-dummy-writes",)]["excluded"] == NO_CONFIRM_SHAPE
    assert dummied.run(("parity-dummy-writes",), {}) == (403, {"error": "excluded"})

    # No UI code names either dummy: the page draws what the list holds.
    templates = resources.files(ui) / "templates"
    for template in templates.iterdir():
        if template.name == "fonts":
            continue  # the pinned font files and licences (M16-51), not UI code
        assert "parity-dummy" not in template.read_text(encoding="utf-8"), template.name


# ─── the checks can fail: negative controls (constructed) ────────────────────

_BAD_CASES: list[tuple[str, Mapping[tuple[str, ...], str], dict[Any, Any], str]] = [
    (
        "flag on by default",
        EXCLUDED,
        dict(REFUSED_PARAMS),
        "parity-bad-default-on: color is a flag that defaults to on",
    ),
    (
        "on/off flag the form drops",
        EXCLUDED,
        dict(REFUSED_PARAMS),
        "parity-bad-on-off: declares 'auto' but the form cannot offer it",
    ),
    (
        "read command with no --json",
        EXCLUDED,
        dict(REFUSED_PARAMS),
        "parity-bad-no-json: declares no --json",
    ),
    (
        "EXCLUDED names no leaf",
        {**EXCLUDED, ("no", "such"): "gone"},
        dict(REFUSED_PARAMS),
        "EXCLUDED no such: not a leaf of the tree",
    ),
    (
        "EXCLUDED names a --yes leaf",
        {**EXCLUDED, ("doctor",): "some other reason"},
        dict(REFUSED_PARAMS),
        "EXCLUDED doctor: declares --yes",
    ),
    (
        "EXCLUDED with an empty reason",
        {**EXCLUDED, ("snap", "list"): " "},
        dict(REFUSED_PARAMS),
        "EXCLUDED snap list: no written reason",
    ),
    (
        "REFUSED_PARAMS names no leaf",
        EXCLUDED,
        {**REFUSED_PARAMS, ("no", "such"): {"x": "gone"}},
        "REFUSED_PARAMS no such: not a leaf of the tree",
    ),
    (
        "REFUSED_PARAMS on an excluded leaf",
        EXCLUDED,
        {**REFUSED_PARAMS, ("ui",): {"root": "moot"}},
        "REFUSED_PARAMS ui: the leaf is excluded",
    ),
    (
        "REFUSED_PARAMS names an undeclared parameter",
        EXCLUDED,
        {**REFUSED_PARAMS, ("looks", "page"): {**REFUSED_PARAMS[("looks", "page")], "nope": "x"}},
        "REFUSED_PARAMS looks page nope: not a declared parameter",
    ),
    (
        "REFUSED_PARAMS reason differs from the server's",
        EXCLUDED,
        {**REFUSED_PARAMS, ("log", "tail"): {"follow": "because"}},
        "REFUSED_PARAMS log tail follow: server reason 'a run must end', want 'because'",
    ),
    (
        "the server refuses what the list does not name",
        EXCLUDED,
        {k: v for k, v in REFUSED_PARAMS.items() if k != ("log", "tail")},
        "log tail: follow refused 'a run must end', want None",
    ),
]


@pytest.mark.parametrize(
    ("excluded", "refused", "fragment"),
    [case[1:] for case in _BAD_CASES],
    ids=[f"constructed: {case[0]}" for case in _BAD_CASES],
)
def test_parity_fails_on_what_the_form_cannot_honestly_offer(
    bad: _Served,
    excluded: Mapping[tuple[str, ...], str],
    refused: Mapping[tuple[str, ...], Mapping[str, str]],
    fragment: str,
) -> None:
    problems = _parity_problems(_tree_with_bad_dummies(), bad, excluded, refused)
    assert any(fragment in p for p in problems), problems


# ─── the page draws forms from the list, and text only ───────────────────────

# Every way page.js could turn text into markup or code. `.on<event> =` is
# matched by event name, so a property such as `.only =` is not a handler.
_EVENTS = (
    r"abort|animation\w*|auxclick|before\w*|blur|cancel|canplay\w*|change|click|close"
    r"|contextmenu|copy|cuechange|cut|dblclick|drag\w*|drop|durationchange|emptied|ended"
    r"|error|focus\w*|formdata|fullscreen\w*|hashchange|input|invalid|key\w*|languagechange"
    r"|load\w*|message\w*|mouse\w*|offline|online|page\w*|paste|pause|play\w*|pointer\w*"
    r"|popstate|progress|ratechange|readystatechange|rejectionhandled|reset|resize|scroll\w*"
    r"|search|seek\w*|select\w*|show|slotchange|stalled|storage|submit|suspend|timeupdate"
    r"|toggle|touch\w*|transition\w*|unhandledrejection|unload|volumechange|waiting|wheel"
)
_HTML_SINKS = re.compile(
    r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\s*\("
    r"|\bFunction\s*\("  # `Function(...)` and `new Function(...)`
    r"|\bsetHTMLUnsafe\s*\(|\bparseHTMLUnsafe\s*\(|\.setHTML\s*\("
    r"|\bset(?:Timeout|Interval)\s*\(\s*[\"'`]"  # a string run as code
    r"|setAttribute\(\s*[\"'`](?:on|href[\"'`]|src[\"'`]|srcdoc|action|formaction|xlink:href)"
    rf"|\.on(?:{_EVENTS})\s*=(?!=)"
    r"|srcdoc|createContextualFragment|DOMParser"
)

_SINK_HITS = [
    "node.innerHTML = text;",
    "node.outerHTML = text;",
    'node.insertAdjacentHTML("beforeend", text);',
    "document.write(text);",
    "eval(text);",
    "new Function(text)();",
    "Function(text)();",
    "const f = Function ('return 1');",
    "node.setHTMLUnsafe(text);",
    "Document.parseHTMLUnsafe(text);",
    "node.setHTML(text);",
    'setTimeout("run()", 10);',
    "setTimeout('run()', 10);",
    "setInterval(`run()`, 10);",
    'a.setAttribute("href", url);',
    "a.setAttribute('src', url);",
    'a.setAttribute("onclick", code);',
    'a.setAttribute("srcdoc", html);',
    "button.onclick = run;",
    "img.onerror=run;",
    "frame.srcdoc = html;",
    "range.createContextualFragment(text);",
    "new DOMParser();",
]

_SINK_MISSES = [
    "test.only = true;",
    "const only = 1; x.once = 2;",
    "node.textContent = text;",
    "setTimeout(() => run(), 10);",
    "setInterval(run, 10);",
    'button.setAttribute("aria-pressed", "false");',
    'a.setAttribute("hreflang", "en");',
    "if (button.onclick === null) {}",
    'node.addEventListener("click", run);',
    "const fn = makeFunction(x);",
]


@pytest.mark.parametrize("snippet", _SINK_HITS, ids=lambda s: f"constructed: {s}")
def test_the_sink_scan_catches(snippet: str) -> None:
    assert _HTML_SINKS.search(snippet), snippet


@pytest.mark.parametrize("snippet", _SINK_MISSES, ids=lambda s: f"constructed: {s}")
def test_the_sink_scan_lets_through(snippet: str) -> None:
    assert _HTML_SINKS.search(snippet) is None, _HTML_SINKS.search(snippet)


def _template(name: str) -> str:
    return (resources.files(ui) / "templates" / name).read_text(encoding="utf-8")


def test_the_page_draws_forms_from_the_command_list_with_text_only() -> None:
    script = _template("page.js")
    assert '"/api/commands"' in script
    assert '"/api/run"' in script
    # The one sink (M14-04): the Looks frame's `srcdoc`, sandboxed with
    # scripts only, given the text /api/looks-page sent (test_ui_looks.py).
    looks_srcdoc = "frame.srcdoc = read.page;"
    assert script.count(looks_srcdoc) == 1
    assert _HTML_SINKS.findall(script.replace(looks_srcdoc, "")) == []


def test_every_element_the_script_looks_up_is_in_the_page() -> None:
    script = _template("page.js")
    page = _template("page.html")
    wanted = set(re.findall(r"byId\(\s*\"([^\"]+)\"\s*\)", script))
    assert {"commands", "fields", "run", "result-tree", "result-raw", "stdout"} <= wanted
    present = set(re.findall(r"\bid=\"([^\"]+)\"", page))
    assert wanted - present == set()


def test_no_template_names_another_origin() -> None:
    # The one exception (M15-02): the dressing-room origin page.js checks the
    # link's address against (test_ui_explorer.py, DRESSING_ORIGIN).
    origin = 'const DRESSING_ORIGIN = "https://www.wowhead.com";'
    assert _template("page.js").count(origin) == 1
    for name in ("page.html", "page.js", "page.css"):
        text = _template(name).replace(origin, "")
        assert re.search(r"(?i)\b(?:https?:|wss?:|ftp:)//|[\"'(]\s*//[a-z0-9]", text) is None, name
