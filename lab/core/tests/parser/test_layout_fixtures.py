"""`layout` and `classify()` against the captured tree (L1, L6, L8).

The captured tree is every install file in the fixture index, laid out as
captured (`<platform>/.build.info`, `<platform>/<flavor-kind>/…`). The tests
iterate the index, so a new capture is inventoried and classified the day
its rows land, and fails here until every path it brings either has a
file-map row or is named in `KNOWN_UNCLASSIFIED` with the reason it is a
stated omission. Facts about the Forever beta capture of 2026-09-22 are
pinned to named paths, never to totals, so a later capture adds to them
without breaking them.

The fixture tree is read in place (reads only). The one end-to-end test
copies it into `tmp_path` with each flavor folder named as its provenance
row says, because discovery only reports `_*_` folders.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from wowlab_core.install import read_install
from wowlab_core.layout import Classified, Inventory, Layout, classify

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURES / "README.md"
NOT_INSTALL = frozenset({"wago", "incoming"})

# Paths (relative to fixtures/) that classify() leaves Unclassified on
# purpose, each with the reason. Empty: every captured path has a row.
KNOWN_UNCLASSIFIED: dict[str, str] = {}


def _index() -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = {"kind": cells[1], "flavor": cells[2]}
    return rows


INDEX_ROWS = _index()
INSTALL_FILES = sorted(p for p in INDEX_ROWS if p.split("/", 1)[0] not in NOT_INSTALL)
PLATFORMS = sorted({p.split("/", 1)[0] for p in INSTALL_FILES})


def _flavor_dirs(platform: str) -> list[Path]:
    return sorted(d.parent for d in (FIXTURES / platform).glob("*/.flavor.info"))


def _layout_for(path: str) -> Layout | None:
    """The layout of the flavor folder holding `path`, if any."""
    platform, _, rest = path.partition("/")
    first = rest.split("/", 1)[0]
    flavor = FIXTURES / platform / first
    if (flavor / ".flavor.info").is_file():
        return Layout(flavor, install_root=FIXTURES / platform)
    return None


def _with_ancestors(paths: list[str]) -> list[str]:
    """Every indexed install file and every folder between it and its platform root."""
    out: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for i in range(2, len(parts) + 1):
            out.add("/".join(parts[:i]))
    return sorted(out)


CAPTURED_PATHS = _with_ancestors(INSTALL_FILES)

# Every path of the Forever beta capture (2026-09-22) and its file-map row.
F = "macos/forever"
A = f"{F}/WTF/Account/90000001#6"
C = f"{A}/1/Labcharb-Labrealmd"
EXPECTED_ENTRY = {
    "macos/.build.info": "build-info",
    F: "flavor-folder",
    f"{F}/.flavor.info": "flavor-info",
    f"{F}/WTF": "wtf-folder",
    f"{F}/WTF/Config.wtf": "config-wtf",
    f"{F}/WTF/Account": "accounts-folder",
    A: "account-folder",
    f"{A}/bindings-cache.wtf": "account-bindings-cache",
    f"{A}/chat-frontend-cache.txt": "account-chat-frontend-cache",
    f"{A}/config-cache.wtf": "account-config-cache",
    f"{A}/edit-mode-cache-account.txt": "account-edit-mode-cache",
    f"{A}/flagged-cache-account.txt": "flagged-cache",
    f"{A}/macros-cache.txt": "account-macros-cache",
    f"{A}/tts-cache-account.txt": "tts-cache",
    f"{A}/1": "numeric-folder",
    C: "second-name-character-folder",
    f"{C}/chat-cache.txt": "character-chat-cache",
    f"{C}/click-bindings-cache.txt": "character-click-bindings-cache",
    f"{C}/config-cache.wtf": "character-wtf-config",
    f"{C}/edit-mode-cache-character.txt": "character-edit-mode-cache",
    f"{C}/flagged-cache-character.txt": "flagged-cache",
    f"{C}/layout-local.txt": "character-layout-local",
    f"{C}/macros-cache.txt": "character-wtf-config",
    f"{C}/tts-cache-character.txt": "tts-cache",
    f"{F}/Interface": "interface-folder",
    f"{F}/Interface/AddOns": "addons-folder",
    f"{F}/Interface/AddOns/DBM-Challenges": "addon",
    f"{F}/Interface/AddOns/DBM-Challenges/DBM-Challenges.toc": "addon",
}


def _classify_in_place(path: str) -> Classified | object:
    layout = _layout_for(path)
    absolute = FIXTURES / path
    if layout is None:  # an install-root file: ask the first flavor's layout
        layout = Layout(_flavor_dirs(path.split("/", 1)[0])[0])
    return layout.classify(absolute)


@pytest.mark.parser
def test_the_captured_tree_is_not_empty() -> None:
    assert "macos" in PLATFORMS
    assert set(EXPECTED_ENTRY) <= set(CAPTURED_PATHS), "a named path left the index"


@pytest.mark.parser
@pytest.mark.parametrize("path", CAPTURED_PATHS)
def test_every_captured_path_is_classified(path: str) -> None:
    got = _classify_in_place(path)
    if path in KNOWN_UNCLASSIFIED:
        assert not isinstance(got, Classified), f"{path} now has a row; drop it from the list"
        return
    assert isinstance(got, Classified), f"{path}: {got}; add a file-map row or a stated omission"


@pytest.mark.parser
@pytest.mark.parametrize(("path", "entry_id"), sorted(EXPECTED_ENTRY.items()))
def test_forever_capture_rows(path: str, entry_id: str) -> None:
    got = _classify_in_place(path)
    assert isinstance(got, Classified)
    assert got.entry.id == entry_id


@pytest.mark.parser
@pytest.mark.parametrize("platform", PLATFORMS)
def test_end_to_end_through_discovery(platform: str, tmp_path: Path) -> None:
    """Copy the capture into an install shaped as captured (flavor folder
    named per the index), discover it, and classify every path through
    `classify(path, install)`, relative and absolute."""
    root = tmp_path / "World of Warcraft"
    captured_as: dict[Path, str] = {}  # copied path -> its path under fixtures/
    for rel in (p for p in INSTALL_FILES if p.startswith(platform + "/")):
        captured = rel.split("/")
        parts = captured[1:]
        if (FIXTURES / platform / parts[0] / ".flavor.info").is_file():
            parts[0] = INDEX_ROWS[rel]["flavor"]
        dest = root.joinpath(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / rel, dest)
        for depth in range(1, len(parts) + 1):  # the file and every folder above it
            captured_as[root.joinpath(*parts[:depth])] = "/".join(captured[: depth + 1])
    install = read_install(root)
    assert install.flavors, "discovery found the flavor folder"
    checked = set()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        got = classify(path, install)
        assert isinstance(got, Classified), f"{rel}: {got}"
        assert classify(rel.as_posix(), install) == got
        captured = captured_as[path]
        if captured in EXPECTED_ENTRY:
            assert got.entry.id == EXPECTED_ENTRY[captured], rel
            checked.add(captured)
    assert checked == {p for p in EXPECTED_ENTRY if p.startswith(platform + "/")}


# ─── inventory ──────────────────────────────────────────────────────────────


def _inventories() -> list[tuple[str, Inventory]]:
    out = []
    for platform in PLATFORMS:
        for flavor in _flavor_dirs(platform):
            layout = Layout(flavor, install_root=FIXTURES / platform)
            out.append((flavor.relative_to(FIXTURES).as_posix(), layout.inventory()))
    return out


INVENTORIES = _inventories()


def _inventoried_paths(inv: Inventory) -> dict[str, str]:
    """{path relative to the flavor folder: which list holds it}."""
    found: dict[str, str] = {}
    for sv in inv.saved_variables:
        found[sv.path] = "saved_variables"
    for w in inv.wtf_files:
        found[w.path] = "wtf_files"
    for addon in inv.addons:
        for toc in addon.tocs:
            found[f"{addon.path}/{toc.file}"] = "addons"
    for o in inv.other.overrides:
        found[o.path] = "overrides"
    return found


@pytest.mark.parser
@pytest.mark.parametrize(("flavor", "inv"), INVENTORIES, ids=[f for f, _ in INVENTORIES])
def test_every_indexed_file_is_inventoried_once(flavor: str, inv: Inventory) -> None:
    found = _inventoried_paths(inv)
    for path in INSTALL_FILES:
        if not path.startswith(flavor + "/"):
            continue
        rel = path.removeprefix(flavor + "/")
        if rel == ".flavor.info":
            continue
        assert rel in found, f"{path} is in no inventory list"
        kind = INDEX_ROWS[path]["kind"]
        if kind == "savedvariables":
            assert found[rel] == "saved_variables", path
        elif kind == "toc":
            assert found[rel] == "addons", path
        elif rel.startswith("WTF/"):
            assert found[rel] == "wtf_files", path
    assert inv.symlinks == ()
    assert inv.errors == ()
    assert inv.truncated is False


@pytest.mark.parser
@pytest.mark.parametrize(("flavor", "inv"), INVENTORIES, ids=[f for f, _ in INVENTORIES])
def test_inventory_survives_json(flavor: str, inv: Inventory) -> None:
    assert Inventory.model_validate_json(inv.model_dump_json()) == inv


@pytest.mark.parser
@pytest.mark.parametrize(("flavor", "inv"), INVENTORIES, ids=[f for f, _ in INVENTORIES])
def test_every_inventoried_path_carries_its_row(flavor: str, inv: Inventory) -> None:
    for sv in inv.saved_variables:
        assert sv.entry_id is not None, sv.path
    for w in inv.wtf_files:
        assert w.entry_id is not None or f"{flavor}/{w.path}" in KNOWN_UNCLASSIFIED, w.path
    for o in inv.other.overrides:
        assert o.entry_id is not None, o.path


@pytest.fixture(scope="module")
def forever() -> Inventory:
    return dict(INVENTORIES)[F]


@pytest.mark.parser
def test_forever_account_realm_and_character(forever: Inventory) -> None:
    (account,) = [a for a in forever.accounts if a.folder == "90000001#6"]
    assert account.path == "WTF/Account/90000001#6"
    assert set(account.files) >= {
        "bindings-cache.wtf",
        "chat-frontend-cache.txt",
        "config-cache.wtf",
        "edit-mode-cache-account.txt",
        "flagged-cache-account.txt",
        "macros-cache.txt",
        "tts-cache-account.txt",
    }
    (realm,) = [r for r in account.realms if r.folder == "1"]
    assert realm.kind == "numeric", "the digits folder is not a realm name"
    (character,) = [c for c in realm.characters if c.folder == "Labcharb-Labrealmd"]
    assert character.shape == "numeric_folder"
    assert character.realm_folder == "1"
    assert (character.first_name, character.second_name) == ("Labcharb", "Labrealmd")
    assert set(character.files) >= {
        "chat-cache.txt",
        "click-bindings-cache.txt",
        "config-cache.wtf",
        "edit-mode-cache-character.txt",
        "flagged-cache-character.txt",
        "layout-local.txt",
        "macros-cache.txt",
        "tts-cache-character.txt",
    }
    assert "AddOns.txt" not in character.files, "AddOns.txt lives in the twin (not captured)"


@pytest.mark.parser
def test_forever_wtf_files_and_scopes(forever: Inventory) -> None:
    by_path = {w.path: w for w in forever.wtf_files}
    config = by_path["WTF/Config.wtf"]
    assert (config.scope, config.account, config.entry_id) == ("machine", None, "config-wtf")
    acct = by_path["WTF/Account/90000001#6/bindings-cache.wtf"]
    assert (acct.scope, acct.account, acct.realm_folder) == ("account", "90000001#6", None)
    char = by_path["WTF/Account/90000001#6/1/Labcharb-Labrealmd/macros-cache.txt"]
    assert (char.scope, char.account, char.realm_folder, char.character_folder) == (
        "character",
        "90000001#6",
        "1",
        "Labcharb-Labrealmd",
    )
    assert char.entry_id == "character-wtf-config"
    empty = by_path["WTF/Account/90000001#6/macros-cache.txt"]
    assert empty.size == 0, "the account macros file is 0 bytes in the capture"
    named = [p.removeprefix(F + "/") for p in EXPECTED_ENTRY if p.startswith(F + "/WTF/")]
    files = [p for p in named if (FIXTURES / F / p).is_file()]
    assert set(files) <= set(by_path)
    assert not any(sv.path in files for sv in forever.saved_variables)


@pytest.mark.parser
def test_forever_addon_and_its_toc(forever: Inventory) -> None:
    (addon,) = [a for a in forever.addons if a.name == "DBM-Challenges"]
    assert addon.blizzard is False
    assert [t.file for t in addon.tocs] == ["DBM-Challenges.toc"]
    toc = addon.tocs[0]
    assert (toc.suffix, toc.matches_folder, toc.error) == (None, True, None)
    assert (addon.selection, addon.selected_toc) == ("single", "DBM-Challenges.toc")
    assert toc.document is not None
    raw = (FIXTURES / F / addon.path / toc.file).read_bytes()
    assert toc.document.to_bytes() == raw
    assert toc.size == len(raw)
    assert toc.document.saved_variables == ("DBMChallenges_AllSavedVars",)


@pytest.mark.parser
def test_forever_other_areas_absent(forever: Inventory) -> None:
    """The capture holds no caches, logs, screenshots, fonts or overrides."""
    areas = {a.name: a for a in forever.other.areas}
    for name in ("Cache", "Logs", "Screenshots", "Errors", "Fonts"):
        if not (FIXTURES / F / name).exists():
            assert areas[name].present is False
    assert all(o.path.startswith(("Interface/", "Fonts/")) for o in forever.other.overrides)
