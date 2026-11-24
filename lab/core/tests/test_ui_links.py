"""Outside links in `wowlab ui` (M21-03): the served allowlist and the saved links.

Two kinds of test, none touching a real install:

- The server, against the real CLI through `test_ui_confirm`'s shim (user
  data directory in `tmp_path`, no client running), on the Forever launch
  client's capture (`macos-70888/`) copied into `tmp_path`: `/api/commands`
  carries `link_origins`, the shipped `link_sites.toml`'s sites; `char link
  set` and `forget` are in the confirm step (a plan run with `--dry-run`, a
  token, a confirmed run with `--yes`); the confirmed run writes only
  `links.json` in the user data directory; the CSP is the policy of before
  M21-03 (only the page's own script hash differs).
- The page, under Node with the Character tab's harness of
  `test_ui_dressing.py` (HTML sinks throw, `<a>` cannot be created,
  navigation and requests outside the server's API are trapped), fed what
  the real CLI printed for the M15-01 capture, with `char gear`'s
  `saved_links` replaced by addresses (the owner's two recorded
  ForeverTwinking examples, a Warcraft Logs address of the ticket's shape).
  Cases labelled `constructed` make an address or the served allowlist
  hostile: an origin outside the list, `http:`, a user name, an address the
  URL parser would rewrite; each saved address is then shown as plain text
  with no href, and an allowlist entry that is not an https origin is
  dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_ui_character as character_tab
import test_ui_dressing as dress
import test_ui_explorer as first_stage
from test_ui_character import _STEP_ANCHOR, _VIEW_ANCHOR, _report, _scenario, _set_report
from test_ui_confirm import _SHIM, _confirm, _plan, _serving, _token
from test_ui_explorer import FLAVOR, _node

from wowlab_core import install, links, ui

home = first_stage.home
server = first_stage.server
forever = dress.forever

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FT = "https://forevertwinking.com/armory/13-sandgraal-rizzen"
BUILD = "https://forevertwinking.com/builds/dwhe4ngw2p"
WCL = "https://forever.warcraftlogs.com/reports/aBcD1234eFgH5678"
SET = ("char", "link", "set")
FORGET = ("char", "link", "forget")
LAUNCH_FLAVOR = "_forever_"  # macos-70888's provenance row; tests may name it (L6)


# ─── the server ──────────────────────────────────────────────────────────────


def test_the_commands_carry_the_shipped_allowlist(server: Any) -> None:
    served = first_stage._send(server, "GET", "/api/commands").json()
    assert served["link_origins"] == links.origins()
    assert set(served["link_origins"]) >= {"blizzard", "wowhead", "forevertwinking"}


# The policy before M21-03, with the page's own script and style hashes
# taken out: M21-03 changed the script, so its hash, and nothing else.
_CSP_SHAPE = (
    "default-src 'none'; script-src <hash> <hash>; style-src <hash> <hash>; "
    "font-src data:; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'none'"
)


def test_the_csp_is_the_same_policy(server: Any) -> None:
    csp = first_stage._page(server).headers["Content-Security-Policy"]
    assert re.sub(r"'sha256-[A-Za-z0-9+/=]+'", "<hash>", csp) == _CSP_SHAPE
    for origin in links.origins().values():
        assert origin not in csp


@pytest.fixture
def real_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ui.UiServer, Path, Path]]:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    data = tmp_path / "m21-03-userdata"
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos-70888" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70888" / "forever", root / LAUNCH_FLAVOR)
    with _serving(wowlab_argv=[sys.executable, "-c", _SHIM, str(data)]) as served:
        yield served, data, root


def _state(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_set_and_forget_go_through_the_confirm_step(
    real_cli: tuple[ui.UiServer, Path, Path],
) -> None:
    served, data, root = real_cli
    before = _state(root)
    params: dict[str, object] = {
        "root": str(root),
        "character": "1/Sandgraal-Rizzen",
        "address": FT,
    }
    plan = _plan(served, SET, params)
    assert plan["exit_code"] == 0, plan["stderr"]
    report = json.loads(plan["stdout"])
    assert report["dry_run"] is True and report["changed"] is True
    assert plan["confirm"]["plan_lines"] == report["plan_lines"]
    assert not (data / "links.json").exists()
    token = _token(plan)
    assert token is not None
    status, done = _confirm(served, SET, token, params)
    assert status == 200 and done["exit_code"] == 0, done
    after = json.loads(done["stdout"])
    assert [r["url"] for r in after["links"]] == [FT]
    stored = json.loads((data / "links.json").read_text(encoding="utf-8"))
    assert stored["characters"][0]["links"][0]["url"] == FT
    # The token is spent.
    status, _again = _confirm(served, SET, token, params)
    assert status == 403
    # Forget, the same way.
    forget: dict[str, object] = {
        "root": str(root),
        "character": "1/Sandgraal-Rizzen",
        "site": "forevertwinking",
    }
    plan = _plan(served, FORGET, forget)
    token = _token(plan)
    assert token is not None
    status, done = _confirm(served, FORGET, token, forget)
    assert status == 200 and done["exit_code"] == 0, done
    assert json.loads(done["stdout"])["links"] == []
    assert _state(root) == before


def test_a_refused_address_is_offered_no_confirm(
    real_cli: tuple[ui.UiServer, Path, Path],
) -> None:
    served, data, root = real_cli
    params: dict[str, object] = {
        "root": str(root),
        "character": "1/Sandgraal-Rizzen",
        "address": "https://evil.example/armory/13-x",
    }
    plan = _plan(served, SET, params)
    assert plan["exit_code"] == 2 and "not kept" in plan["stderr"]
    assert _token(plan) is None
    assert not (data / "links.json").exists()


# ─── the page ────────────────────────────────────────────────────────────────

_SAVED_VIEW = r"""
ctx.URL = URL;
function savedSlot(id) {
  const link = ids[id];
  return {
    boxHidden: ids[id + "-box"].hidden,
    linkHidden: link.hidden,
    href: typeof link.href === "string" ? link.href : null,
    hrefAttr: link.getAttribute("href"),
    text: ids[id + "-text"].textContent,
  };
}
function savedView() {
  return {
    savedView: true,
    hidden: ids["char-saved"].hidden,
    none: !ids["char-saved-none"].hidden,
    ft: savedSlot("char-saved-ft-profile"),
    build: savedSlot("char-saved-ft-build"),
    wcl: savedSlot("char-saved-wcl-report"),
    status: ids["char-saved-status"].textContent,
    confirmHidden: ids["char-saved-confirm-box"].hidden,
    plan: ids["char-saved-plan-lines"].textContent,
    buttonHidden: ids["char-saved-confirm"].hidden,
    dressingHref: typeof ids["char-dressing-link"].href === "string" ? ids["char-dressing-link"].href : null,
  };
}
"""

_SAVED_STEPS = r"""
      } else if (step.saved !== undefined) {
        out.steps.push(Object.assign(savedView(), { requestsSoFar: requests.slice() }));
      } else if (step.saved_type !== undefined) {
        ids["char-saved-address"].value = step.saved_type;
      } else if (step.saved_click !== undefined) {
        const before = requests.length;
        ids[step.saved_click].fire("click"); await tick();
        out.steps.push(Object.assign(savedView(), { newRequests: requests.slice(before) }));
"""

# The stand-in server answers a plan run and its confirmed run apart, and
# gives a plan run the confirm offer the real server would (`offer`).
_MATCH = "canon(run.command) === canon(body.command) && canon(run.params) === canon(params)"
_STAGED = (
    _MATCH
    + ' && (run.stage === undefined || (run.stage === "confirmed") === Boolean(body.confirm))'
)
_ANSWER = (
    "return answer({ exit_code: code, timed_out: false, truncated: false, outcome: outcome(code), "
    "stdout: stdout, stderr: stderr });"
)


def _harness() -> str:
    text = character_tab._harness()
    assert text.count(_STEP_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    for old in (_MATCH, "      stdout = run.stdout;\n", "  let stdout = null;\n", _ANSWER):
        assert text.count(old) == 1, old
    text = text.replace(_MATCH, _STAGED, 1)
    text = text.replace(
        "      stdout = run.stdout;\n",
        "      stdout = run.stdout; offered = run.offer || null;\n",
        1,
    )
    text = text.replace(
        "  let stdout = null;\n", "  let stdout = null;\n  let offered = null;\n", 1
    )
    text = text.replace(
        _ANSWER, _ANSWER.replace("stderr: stderr }", "stderr: stderr, confirm: offered }"), 1
    )
    text = text.replace(_VIEW_ANCHOR, _SAVED_VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_STEP_ANCHOR, _SAVED_STEPS.lstrip("\n") + _STEP_ANCHOR, 1)


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m21-03-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m21-03-scenario.json"
    data.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run(
        [_node(), str(harness), str(_TEMPLATES / "page.js"), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert "error" not in out, out["error"]
    assert not out["traps"], out["traps"]
    return out


def _row(site: str, kind: str, label: str, url: str) -> dict[str, str]:
    return {
        "flavor_folder": FLAVOR,
        "account": dress.ACCOUNT,
        "character": dress.SANGRAAL,
        "site": site,
        "site_name": label,
        "kind": kind,
        "label": label,
        "url": url,
        "saved_at": "2026-11-23T12:00:00Z",
    }


ROWS = [
    _row("forevertwinking", "profile", "ForeverTwinking", FT),
    _row("forevertwinking", "build", "ForeverTwinking build", BUILD),
    _row("warcraftlogs", "report", "Warcraft Logs report", WCL),
]


def _with_saved(server: Any, steps: list[dict[str, Any]], rows: list[dict[str, str]]) -> Any:
    scenario = _scenario(server, steps, [dress.SANGRAAL])
    report = _report(scenario, dress.SANGRAAL)
    assert report["saved_links"] == [] or isinstance(report["saved_links"], list)
    report["saved_links"] = rows
    _set_report(scenario, dress.SANGRAAL, report)
    return scenario


def _saved_steps(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in out["steps"] if s.get("savedView")]


def _only_api(out: dict[str, Any]) -> None:
    for step in out["steps"]:
        for request in step.get("requestsSoFar", []) + step.get("newRequests", []):
            assert request["url"].startswith(("/api/commands", "/api/run")), request


def test_each_saved_address_is_its_own_static_link(
    server: Any, dress_forever: Path, tmp_path: Path
) -> None:
    scenario = _with_saved(server, [{"char_tab": True}, {"saved": True}], ROWS)
    out = _drive(tmp_path / "node", scenario)
    view = _saved_steps(out)[-1]
    assert view["hidden"] is False and view["none"] is False
    for slot, url in (("ft", FT), ("build", BUILD), ("wcl", WCL)):
        assert view[slot]["boxHidden"] is False and view[slot]["linkHidden"] is False
        assert view[slot]["href"] == url and view[slot]["text"] == ""
    # The dressing room still links, checked against the served Wowhead origin.
    assert view["dressingHref"] == dress.ADDRESSES[dress.SANGRAAL]
    _only_api(out)


def test_no_saved_address_says_none(server: Any, dress_forever: Path, tmp_path: Path) -> None:
    scenario = _with_saved(server, [{"char_tab": True}, {"saved": True}], [])
    view = _saved_steps(_drive(tmp_path / "node", scenario))[-1]
    assert view["hidden"] is False and view["none"] is True
    assert view["ft"]["boxHidden"] is True and view["ft"]["hrefAttr"] is None


_HOSTILE = [
    "https://evil.example/armory/13-sandgraal-rizzen",  # an origin outside the list
    "https://forever.warcraftlogs.com/reports/x",  # another site's origin in this slot
    "http://forevertwinking.com/armory/13-sandgraal-rizzen",
    "https://u:p@forevertwinking.com/armory/13-sandgraal-rizzen",
    "https://forevertwinking.com@evil.example/armory/13-x",
    "https://FOREVERTWINKING.COM/armory/13-sandgraal-rizzen",  # rewritten by the parser
    "https://forevertwinking.com:443/armory/13-sandgraal-rizzen",  # rewritten by the parser
    "https://forevertwinking.com/armory/13 x",  # rewritten by the parser
    "javascript:alert(1)",
    "//forevertwinking.com/armory/13-x",
]


@pytest.mark.parametrize("url", _HOSTILE, ids=lambda u: f"constructed: {u!r}")
def test_a_hostile_saved_address_is_plain_text_constructed(
    server: Any, dress_forever: Path, tmp_path: Path, url: str
) -> None:
    rows = [_row("forevertwinking", "profile", "ForeverTwinking", url)]
    scenario = _with_saved(server, [{"char_tab": True}, {"saved": True}], rows)
    view = _saved_steps(_drive(tmp_path / "node", scenario))[-1]
    ft = view["ft"]
    assert ft["href"] is None and ft["hrefAttr"] is None and ft["linkHidden"] is True
    assert ft["boxHidden"] is False
    assert ft["text"].startswith(url.replace("\n", "")) and "not shown as a link" in ft["text"]


@pytest.mark.parametrize(
    "origins",
    [
        {"forevertwinking": "http://forevertwinking.com"},
        {"forevertwinking": "https://forevertwinking.com/"},
        {"forevertwinking": "https://u@forevertwinking.com"},
        {"forevertwinking": "https://forevertwinking.com/armory"},
        {"forevertwinking": 5},
        {"Forevertwinking": "https://forevertwinking.com"},
        {},
        [],
        "https://forevertwinking.com",
    ],
    ids=lambda o: f"constructed: {o!r}",
)
def test_an_allowlist_entry_that_is_not_an_https_origin_is_dropped_constructed(
    server: Any, dress_forever: Path, tmp_path: Path, origins: Any
) -> None:
    scenario = _with_saved(server, [{"char_tab": True}, {"saved": True}], ROWS[:1])
    scenario["commands"]["link_origins"] = origins
    view = _saved_steps(_drive(tmp_path / "node", scenario))[-1]
    assert view["ft"]["href"] is None and view["ft"]["linkHidden"] is True
    assert "not shown as a link" in view["ft"]["text"]
    # No Wowhead origin served either: the dressing room draws no link.
    assert view["dressingHref"] is None


def _params(**extra: str) -> dict[str, str]:
    return {"character": dress.SANGRAAL, "account": dress.ACCOUNT, "flavor": FLAVOR, **extra}


def _plan_run(command: tuple[str, ...], params: dict[str, str], report: Any) -> dict[str, Any]:
    return {
        "command": list(command),
        "params": params,
        "stage": "plan",
        "stdout": json.dumps(report),
        "offer": {
            "stage": "plan",
            "token": "t" * 43,
            "plan_lines": report["plan_lines"],
            "note": None,
            "label": "Confirm",
        },
    }


def test_a_pasted_address_is_planned_then_confirmed(
    server: Any, dress_forever: Path, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [
        {"char_tab": True},
        {"saved": True},
        {"saved_type": "  " + FT + " "},
        {"saved_click": "char-saved-plan"},
        {"saved_click": "char-saved-confirm"},
    ]
    scenario = _with_saved(server, steps, [])
    params = _params(address=FT)
    line = f"ForeverTwinking for {dress.SANGRAAL}: <b>{FT}</b>"
    plan = {"dry_run": True, "changed": True, "plan_lines": [line], "links": []}
    done = {"dry_run": False, "changed": True, "plan_lines": [line], "links": ROWS[:1]}
    scenario["runs"] += [
        _plan_run(SET, params, plan),
        {"command": list(SET), "params": params, "stage": "confirmed", "stdout": json.dumps(done)},
    ]
    out = _drive(tmp_path / "node", scenario)
    first, planned, confirmed = [s for s in out["steps"] if s.get("savedView")]
    assert first["none"] is True
    assert [r["body"] for r in planned["newRequests"]] == [{"command": list(SET), "params": params}]
    assert planned["confirmHidden"] is False and planned["buttonHidden"] is False
    assert planned["plan"] == line  # text, never markup
    assert [r["body"] for r in confirmed["newRequests"]] == [
        {"command": list(SET), "params": params, "confirm": "t" * 43}
    ]
    assert confirmed["ft"]["href"] == FT and confirmed["status"] == "Saved."
    assert confirmed["confirmHidden"] is True
    _only_api(out)


def test_forget_is_planned_then_confirmed(server: Any, dress_forever: Path, tmp_path: Path) -> None:
    steps: list[dict[str, Any]] = [
        {"char_tab": True},
        {"saved_click": "char-saved-wcl-report-forget"},
        {"saved_click": "char-saved-confirm"},
    ]
    scenario = _with_saved(server, steps, ROWS)
    params = _params(site="warcraftlogs", kind="report")
    plan = {"dry_run": True, "changed": True, "plan_lines": ["Drop it"], "links": ROWS}
    done = {"dry_run": False, "changed": True, "plan_lines": ["Drop it"], "links": ROWS[:2]}
    scenario["runs"] += [
        _plan_run(FORGET, params, plan),
        {
            "command": list(FORGET),
            "params": params,
            "stage": "confirmed",
            "stdout": json.dumps(done),
        },
    ]
    out = _drive(tmp_path / "node", scenario)
    planned, confirmed = [s for s in out["steps"] if s.get("savedView")]
    assert planned["wcl"]["href"] == WCL and planned["buttonHidden"] is False
    assert confirmed["wcl"]["boxHidden"] is True and confirmed["wcl"]["hrefAttr"] is None
    assert confirmed["ft"]["href"] == FT and confirmed["status"] == "Forgotten."


def test_a_plan_that_changes_nothing_offers_no_confirm(
    server: Any, dress_forever: Path, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [
        {"char_tab": True},
        {"saved_type": FT},
        {"saved_click": "char-saved-plan"},
    ]
    scenario = _with_saved(server, steps, ROWS[:1])
    plan = {"dry_run": True, "changed": False, "plan_lines": ["already"], "links": ROWS[:1]}
    scenario["runs"].append(_plan_run(SET, _params(address=FT), plan))
    view = [s for s in _drive(tmp_path / "node", scenario)["steps"] if s.get("savedView")][-1]
    assert view["buttonHidden"] is True and view["confirmHidden"] is True
    assert view["status"] == "Nothing would change: it is already so."


@pytest.fixture
def dress_forever(forever: Path) -> Path:
    return forever
