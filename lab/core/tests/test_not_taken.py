"""Why a `_*_` folder was not taken as a flavor, in text and `--json` (M13-45).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
with its flavor folder named as its provenance row says (`_classic_beta_`),
and `--root` pointing at the copy. Nothing reads or writes a real install.

Constructed (L8, boundary): the link `_retail_` beside the real flavor folder
(a symlink to a second copy of the captured flavor folder outside the
install), the empty folder `_empty_`, a refused `lstat`, and a link whose
text cannot be read (stood in by a link test that says yes for a real
folder, as `os.readlink` cannot read a WCI link or a WSL symlink).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from wowlab_core import cli, doctorredact, gaps, install, redact, snapshot
from wowlab_core.install import NotTaken, discover, read_install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
LINKED = "_retail_"  # constructed: a link beside it
EMPTY = "_empty_"  # constructed: a folder with no .flavor.info
VERDICT_LINK = (
    "no file-map row; a link, or inside one; wowlab never follows it or writes "
    "through it, and a restore does not put it back"
)


@pytest.fixture(autouse=True)
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no client in it."""
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


@pytest.fixture(autouse=True)
def userdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import platformdirs

    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)


def _install(tmp_path: Path, *, real: bool = True, empty: bool = False) -> tuple[Path, Path]:
    """(install root, where the link points). The root holds the real
    flavor folder when `real`, `_empty_` when `empty`, and `_retail_`, a
    symlink to a copy of the captured flavor folder outside the install."""
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    if real:
        shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    if empty:
        (root / EMPTY).mkdir()
    target = tmp_path / "elsewhere" / "retail"
    shutil.copytree(CAPTURE / "forever", target)
    try:
        (root / LINKED).symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    return root, target


def _run(*args: str, code: int = 0) -> Any:
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == code, (args, result.output, result.exception)
    return result


def _link_note(target: Path) -> str:
    return f"link to {target}, not followed; {gaps.NOT_A_FLAVOR_LINK}"


# ─── the model ───────────────────────────────────────────────────────────────


def test_each_folder_not_taken_carries_its_reason(tmp_path: Path) -> None:
    root, target = _install(tmp_path, empty=True)

    got = discover(root, environ={}, defaults=[])

    assert [f.folder for f in got.flavors] == [FLAVOR]
    assert got.other_dirs == (EMPTY, LINKED)
    assert got.not_taken == (
        NotTaken(name=EMPTY, reason="no_flavor_info", target=None, note=gaps.NOT_A_FLAVOR),
        NotTaken(name=LINKED, reason="link", target=str(target), note=_link_note(target)),
    )
    assert install.not_taken_why(got.not_taken[1]) == f"link to {target}, not followed"
    # The words moved to `install`; `gaps` re-exports them unchanged.
    assert (gaps.NOT_A_FLAVOR, gaps.NOT_A_FLAVOR_LINK) == (
        install.NOT_A_FLAVOR,
        install.NOT_A_FLAVOR_LINK,
    )
    assert install.Install.model_validate_json(got.model_dump_json()) == got


def test_a_link_whose_text_cannot_be_read_has_no_target_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: a WCI link or a WSL symlink is a link by `is_link`, and
    `os.readlink` cannot read it (stood in by a real folder that the link
    test calls a link, whose `readlink` fails as a folder's does)."""
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    real_is_link = install.is_link
    monkeypatch.setattr(
        install, "is_link", lambda path, st: path.name == FLAVOR or real_is_link(path, st)
    )

    got = read_install(root)

    assert got.flavors == ()
    assert got.not_taken == (
        NotTaken(
            name=FLAVOR,
            reason="link",
            target=None,
            note=f"link (where it points is unknown), not followed; {gaps.NOT_A_FLAVOR_LINK}",
        ),
    )


def test_a_refused_lstat_could_not_be_checked_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    (root / "_racing_").mkdir()
    racing, real_lstat = root / "_racing_", Path.lstat

    def lstat(self: Path) -> Any:
        if self == racing:
            raise PermissionError(13, "refused")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", lstat)

    got = read_install(root)

    assert got.not_taken == (
        NotTaken(
            name="_racing_",
            reason="not_checked",
            target=None,
            note="not taken as a flavor folder (it could not be checked: refused)",
        ),
    )
    assert install.not_taken_why(got.not_taken[0]) == "it could not be checked: refused"


def test_an_install_with_no_such_folder_prints_its_json_as_before(tmp_path: Path) -> None:
    """The field is left out of the JSON when empty and reads back as empty."""
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)

    got = read_install(root)
    shown = json.loads(_run("install", "show", "--root", str(root), "--json").stdout)
    doctor = json.loads(_run("doctor", "--offline", "--json", "--root", str(root)).stdout)
    explained = json.loads(
        _run(
            "explain", str(root / FLAVOR / "WTF" / "Config.wtf"), "--root", str(root), "--json"
        ).stdout
    )

    assert got.not_taken == ()
    assert "not_taken" not in got.model_dump(mode="json")
    assert "not_taken" not in shown
    assert "not_taken" not in doctor
    assert "not_taken" not in explained
    assert install.Install.model_validate_json(got.model_dump_json()) == got


def test_not_taken_must_name_other_dirs_constructed(tmp_path: Path) -> None:
    root, _ = _install(tmp_path)
    got = read_install(root)

    with pytest.raises(ValidationError, match="not_taken must name"):
        got.model_validate(got.model_dump() | {"other_dirs": ("_other_",)})


# ─── the five places ─────────────────────────────────────────────────────────


def test_a_flavor_lookup_of_a_linked_folder_says_it_is_a_link(tmp_path: Path) -> None:
    root, target = _install(tmp_path)

    result = _run("sv", "list", "--root", str(root), "--flavor", LINKED, code=2)

    assert result.stderr == (
        f"wowlab: {LINKED!r} in {root}: {_link_note(target)}; "
        f"{install.NOT_TAKEN_LINK}; flavors found: {FLAVOR}\n"
    )
    assert "no flavor folder" not in result.stderr
    # A folder with no .flavor.info: its own note, one bracketed group.
    root2 = tmp_path / "second"
    root2.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root2 / ".build.info")
    shutil.copytree(CAPTURE / "forever", root2 / FLAVOR)
    (root2 / EMPTY).mkdir()
    empty = _run("sv", "list", "--root", str(root2), "--flavor", EMPTY, code=2)
    assert empty.stderr == (
        f"wowlab: {EMPTY!r} in {root2}: {gaps.NOT_A_FLAVOR}; flavors found: {FLAVOR}\n"
    )
    # A name that is nowhere is still "no flavor folder".
    absent = _run("sv", "list", "--root", str(root), "--flavor", "_absent_", code=2)
    assert "no flavor folder '_absent_'" in absent.stderr


@pytest.mark.parametrize("command", [("sv", "list"), ("addons", "list"), ("tree",)])
def test_no_flavor_folders_names_what_was_not_taken(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    root, target = _install(tmp_path, real=False, empty=True)

    result = _run(*command, "--root", str(root), code=1)

    assert result.stderr == (
        f"wowlab: {root} has no flavor folder wowlab can read (a real folder named _..._ "
        "holding a regular .flavor.info); "
        f"not taken: {EMPTY} (no regular .flavor.info file), "
        f"{LINKED} (link to {target}, not followed); {install.NOT_TAKEN_LINK}\n"
    )


@pytest.mark.parametrize("command", [("install", "show"), ("doctor", "--offline")])
def test_doctor_and_install_show_give_each_reason_as_the_json_does(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    root, target = _install(tmp_path, empty=True)

    text = _run(*command, "--root", str(root)).stdout
    data = json.loads(_run(*command, "--root", str(root), "--json").stdout)

    assert data["other_dirs"] == [EMPTY, LINKED]
    assert data["not_taken"] == [
        {"name": EMPTY, "reason": "no_flavor_info", "target": None, "note": gaps.NOT_A_FLAVOR},
        {"name": LINKED, "reason": "link", "target": str(target), "note": _link_note(target)},
    ]
    block = "".join(f"  {n['name']}: {n['note']}\n" for n in data["not_taken"])
    assert f"Folders named _..._ not taken as flavors:\n{block}" in text
    assert "that are not flavors" not in text


def test_doctor_redacts_what_was_not_taken(tmp_path: Path) -> None:
    """`--redact`: the reason stays, the name, target and note are redacted
    (here the scratch folder stands in as the home folder)."""
    root, _ = _install(tmp_path)
    report = cli._doctor(root, True)
    assert [n.name for n in report.not_taken] == [LINKED]

    hidden = doctorredact.redact_model(
        report, redact.Redactor(homes=[str(tmp_path)]), doctorredact.DOCTOR_FIELDS
    )

    assert [n.reason for n in hidden.not_taken] == ["link"]
    assert str(tmp_path) not in hidden.model_dump_json()


def test_the_first_run_says_a_linked_flavor_folder_is_not_snapshotted(tmp_path: Path) -> None:
    root, target = _install(tmp_path, real=False)

    result = _run("doctor", "--offline", "--root", str(root), code=1)
    data = json.loads(_run("doctor", "--offline", "--json", "--root", str(root), code=1).stdout)

    assert (
        f"{root} {install.NO_READABLE_FLAVOR}, so there is nothing to snapshot yet.\n"
        f"  {LINKED} is a link to {target}, not followed, so its WTF (settings and "
        f"SavedVariables), Interface and Fonts are not snapshotted: "
        f"{install.NOT_TAKEN_LINK}. Back up the folder it points to yourself.\n"
    ) in result.stdout
    assert f"{root} {install.NO_READABLE_FLAVOR}" in data["problems"]
    assert f"{root} has no flavor folders" not in data["problems"]


def test_with_nothing_not_taken_the_old_opening_stays(tmp_path: Path) -> None:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")

    refused = _run("sv", "list", "--root", str(root), code=1)
    doctor = _run("doctor", "--offline", "--root", str(root), code=1)

    assert refused.stderr == (
        f"wowlab: {root} has no flavor folders (a folder named _..._ holding .flavor.info)\n"
    )
    assert f"{root} has no flavor folders, so there is nothing to snapshot yet.\n" in doctor.stdout


@pytest.mark.parametrize("absolute", [True, False], ids=["absolute", "relative"])
@pytest.mark.parametrize("below", ["", "WTF/Config.wtf"], ids=["folder", "file-inside"])
def test_explain_of_a_linked_folder_says_link_first_and_not_taken(
    tmp_path: Path, below: str, absolute: bool
) -> None:
    """With one real flavor beside the link, a relative path whose first part
    is the link stays at the install root (it is not re-read in the flavor)."""
    root, target = _install(tmp_path)
    relative = f"{LINKED}/{below}" if below else LINKED
    path = str(root / relative) if absolute else relative

    text = _run("explain", path, "--root", str(root)).stdout
    data = json.loads(_run("explain", path, "--root", str(root), "--json").stdout)

    assert (data["result"]["base"], data["result"]["path"]) == ("root", relative)
    assert (data["verdict"]["link"], data["verdict"]["words"]) == (True, VERDICT_LINK)
    assert data["not_taken"] == {
        "name": LINKED,
        "reason": "link",
        "target": str(target),
        "note": _link_note(target),
    }
    assert text.splitlines()[:2] == [
        f"{path}: {data['verdict']['words']}",
        f"  {LINKED}: {data['not_taken']['note']}",
    ]


@pytest.mark.parametrize("absolute", [True, False], ids=["absolute", "relative"])
def test_explain_of_a_folder_without_flavor_info_says_not_taken(
    tmp_path: Path, absolute: bool
) -> None:
    root, _ = _install(tmp_path, empty=True)
    path = str(root / EMPTY) if absolute else EMPTY

    text = _run("explain", path, "--root", str(root)).stdout
    data = json.loads(_run("explain", path, "--root", str(root), "--json").stdout)

    assert data["result"]["base"] == "root"
    assert data["not_taken"]["note"] == gaps.NOT_A_FLAVOR
    assert text.splitlines()[:2] == [
        f"{path}: no file-map row: no file-map row matches",
        f"  {EMPTY}: {gaps.NOT_A_FLAVOR}",
    ]


# ─── refused at the read (M13-46) ────────────────────────────────────────────


@pytest.mark.parametrize("swap", ["link", "other-file"], ids=lambda s: f"constructed-{s}")
def test_a_flavor_info_refused_at_the_read_changed_never_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str
) -> None:
    """Constructed (a race cannot be captured): the scan saw a regular
    `.flavor.info`, and between the read's `lstat` and its open it became a
    link, or another regular file took its place (refused by the identity
    check). Either way the folder is named with `flavor_info_changed`,
    never worded as a link: the file that took its place may be regular."""
    root, _ = _install(tmp_path)
    info = root / FLAVOR / ".flavor.info"
    real_open = snapshot.open_regular_file

    def open_regular_file(path: Path, **kwargs: Any) -> Any:
        if path == info:
            if swap == "link":
                info.unlink()
                info.symlink_to(tmp_path / "elsewhere" / "retail" / ".flavor.info")
            else:  # renamed aside so the new file cannot reuse its inode
                info.rename(info.parent / "m13-45-original.flavor.info")
                info.write_bytes((CAPTURE / "forever" / ".flavor.info").read_bytes())
        return real_open(path, **kwargs)

    monkeypatch.setattr(snapshot, "open_regular_file", open_regular_file)

    got = read_install(root)

    assert got.flavors == ()
    assert got.other_dirs == (FLAVOR, LINKED)
    assert got.not_taken[0] == NotTaken(
        name=FLAVOR,
        reason="flavor_info_changed",
        target=None,
        note=install.NOT_A_FLAVOR_CHANGED,
    )
    assert "link" not in got.not_taken[0].note
    assert install.not_taken_why(got.not_taken[0]) == (
        "its .flavor.info changed as it was read; run the command again"
    )


@pytest.mark.parametrize(
    ("reason", "note"),
    [
        ("not_checked", "not taken as a flavor folder (it could not be checked: refused)"),
        ("flavor_info_changed", install.NOT_A_FLAVOR_CHANGED),
    ],
)
def test_tree_and_gaps_give_a_folder_its_own_note_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str, note: str
) -> None:
    """Constructed: the folder's reason is stood in on the model discovery
    returns; `tree --install` and `filemap gaps` give its note, not
    `NOT_A_FLAVOR`."""
    root, _ = _install(tmp_path, empty=True)
    real = install.read_install(root)
    entry = NotTaken(name=EMPTY, reason=reason, target=None, note=note)  # type: ignore[arg-type]
    stood = real.model_copy(update={"not_taken": (entry, *real.not_taken[1:])})
    monkeypatch.setattr(install, "discover", lambda *a, **k: stood)
    (root / EMPTY / "x.txt").write_bytes(b"constructed\n")

    tree = json.loads(_run("tree", "--install", "--root", str(root), "--json").stdout)
    gap = json.loads(_run("filemap", "gaps", "--root", str(root), "--json").stdout)

    item = next(i for i in tree["items"] if i["path"] == EMPTY)
    assert item["note"] == note
    assert [g["note"] for g in gap["groups"] if g["name"] == EMPTY] == [note]


@pytest.mark.parametrize("chosen", [FLAVOR, LINKED])
def test_explain_of_a_not_taken_path_with_a_named_flavor_stays_at_the_root(
    tmp_path: Path, chosen: str
) -> None:
    """`--flavor` naming the real flavor or the folder not taken: the path is
    still read at the install root. One naming nothing is refused as on main."""
    root, _ = _install(tmp_path)

    data = json.loads(
        _run("explain", LINKED, "--flavor", chosen, "--root", str(root), "--json").stdout
    )
    nope = _run("explain", LINKED, "--flavor", "_nope_", "--root", str(root), code=2)

    assert (data["result"]["base"], data["not_taken"]["name"]) == ("root", LINKED)
    assert "no flavor folder '_nope_'" in nope.stderr
