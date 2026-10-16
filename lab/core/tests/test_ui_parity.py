"""The UI parity test (M13-26, ADR-0029 and its 2026-10-15 amendment).

`wowlab ui` builds its commands from the CLI's Click tree at start, so a new
command reaches the UI with no UI code. This test keeps that true. It runs in
the required `quality` job, with the rest of `lab/core/tests`.

- Every leaf of the real Click tree is reachable in the UI: listed by `GET
  /api/commands`, not excluded, with every parameter it declares offered to
  the form in a shape the page draws. Otherwise it sits in an exclusion list
  below, with a written reason.
- Every leaf that declares `--yes` is excluded with "confirm flow: M14". The
  set is read from the Click tree here, not counted, and holds `doctor`.
- `ui` itself is excluded.
- `looks page --out` and `log tail --follow` are refused.
- A dummy command added to the tree in the test appears in the UI, runs, and
  needs no UI code.

Nothing here reads or writes an install. The `wowlab` the server starts is a
constructed stand-in (`_ECHO`) that prints its argv as JSON, so a run proves
what the server would have run without running it.
"""

from __future__ import annotations

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

CONFIRM_FLOW = "confirm flow: M14"
"""ADR-0029 amendment (2026-10-15): the reason for every leaf that declares
`--yes`. Those leaves are found in the tree, never listed by hand."""

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

# The kinds of parameter `page.js` draws a field for.
_DRAWN_KINDS = {"argument", "option", "flag"}

# The stand-in `wowlab`: prints the argv it was given, as JSON (constructed).
_ECHO = "import json, sys; print(json.dumps(sys.argv[1:]))"

# The Click parameter the server adds itself, so not one the form offers.
_JSON_OPT = "--json"


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


def _declared(cmd: Any) -> set[str]:
    """The names of the parameters a leaf declares, `--json` aside."""
    return {str(p.name) for p in cmd.params if _JSON_OPT not in _opts(p)}


def _real_tree() -> Any:
    from wowlab_core import cli

    return typer.main.get_command(cli.app)


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


def _expected_exclusions(tree: Any) -> dict[tuple[str, ...], str]:
    expected = {path: CONFIRM_FLOW for path, cmd in _leaves(tree) if _declares_yes(cmd)}
    expected.update(EXCLUDED)
    return expected


def _assert_parity(tree: Any, served: _Served) -> None:
    """Every leaf of `tree` is reachable in the UI or excluded with its reason."""
    leaves = dict(_leaves(tree))
    listed = served.commands()
    assert set(listed) == set(leaves), {
        "leaves the UI does not list": sorted(set(leaves) - set(listed)),
        "listed but not leaves": sorted(set(listed) - set(leaves)),
    }
    excluded = _expected_exclusions(tree)
    problems: list[str] = []
    for path, cmd in leaves.items():
        item = listed[path]
        name = " ".join(path)
        if path in excluded:
            if item["excluded"] != excluded[path]:
                problems.append(f"{name}: excluded {item['excluded']!r}, want {excluded[path]!r}")
            continue
        if item["excluded"] is not None:
            problems.append(f"{name}: excluded {item['excluded']!r} with no written reason here")
            continue
        offered = {p["name"]: p for p in item["params"]}
        missing = _declared(cmd) - set(offered)
        if missing:
            problems.append(f"{name}: declares {sorted(missing)} but the form cannot offer them")
        for param in offered.values():
            if param["kind"] not in _DRAWN_KINDS:
                problems.append(
                    f"{name}: {param['name']} is a {param['kind']!r} the page cannot draw"
                )
            want = REFUSED_PARAMS.get(path, {}).get(param["name"])
            if param["refused"] != want:
                problems.append(
                    f"{name}: {param['name']} refused {param['refused']!r}, want {want!r}"
                )
            if param["required"] and param["refused"]:
                problems.append(f"{name}: {param['name']} is required and refused, so unreachable")
    assert problems == []


# ─── the parity tests ────────────────────────────────────────────────────────


def test_every_leaf_is_reachable_or_excluded_with_a_written_reason(real: _Served) -> None:
    _assert_parity(_real_tree(), real)
    for path, reason in EXCLUDED.items():
        assert reason.strip(), path
    for path, refused in REFUSED_PARAMS.items():
        for name, reason in refused.items():
            assert reason.strip(), (path, name)


def test_every_yes_leaf_is_excluded_for_the_confirm_flow_and_doctor_is_one(real: _Served) -> None:
    tree = _real_tree()
    yes = {path for path, cmd in _leaves(tree) if _declares_yes(cmd)}
    assert ("doctor",) in yes
    listed = real.commands()
    assert {path for path, item in listed.items() if item["excluded"] == CONFIRM_FLOW} == yes
    for path in sorted(yes):
        status, payload = real.run(path, {})
        assert (status, payload) == (403, {"error": "excluded"}), path


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

    assert listed[("parity-dummy-writes",)]["excluded"] == CONFIRM_FLOW
    assert dummied.run(("parity-dummy-writes",), {}) == (403, {"error": "excluded"})

    # No UI code names either dummy: the page draws what the list holds.
    templates = resources.files(ui) / "templates"
    for template in templates.iterdir():
        assert "parity-dummy" not in template.read_text(encoding="utf-8"), template.name


# ─── the page draws forms from the list, and text only ───────────────────────

_HTML_SINKS = re.compile(
    r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\s*\(|new\s+Function"
    r"|setAttribute\(\s*[\"']on|\.on[a-z]+\s*=|srcdoc|createContextualFragment|DOMParser"
)


def test_the_page_draws_forms_from_the_command_list_with_text_only() -> None:
    script = (resources.files(ui) / "templates" / "page.js").read_text(encoding="utf-8")
    assert '"/api/commands"' in script
    assert '"/api/run"' in script
    assert _HTML_SINKS.findall(script) == []
