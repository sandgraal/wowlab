# Probe from review of m10/02-lab-capture; reproduces four ways the scrub tool
# leaves identity, or a wrong file, behind while reporting success.
"""Review probes for `scripts/lab_capture.py` (M10-02).

Everything here is constructed: a synthetic install tree in `tmp_path`, in our
own layout, with invented identity strings (docs/LAB_PLAN.md §9). No test
needs or touches a real install (ADR-0012). Each probe carries a positive
control that passes today, so a red probe means the defect and not the rig.

1. A re-run that now refuses (or passes over) a file leaves the earlier copy
   in `--out`, still carrying the identity string, and exits 0.
2. An identity CVar line that does not start right after a `\\n` (UTF-8 BOM
   on line one, or CR-only line endings) is neither blanked nor reported.
3. Two flavor folders given the same `--kind` overwrite each other silently.
4. A name the filesystem reports in NFD is not found in NFC file contents.
"""

from __future__ import annotations

import importlib.util
import sys
import unicodedata
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"

ACCOUNT = "123456789#1"
PSEUDO_ACCOUNT = "90000001#1"
REALM = "Area 52"
CHARACTER = "Thrallmar"
GUILD = "Knights of Foo"
GUILD_RETITLED = "Knights Of Foo"  # the casing an addon's title-case helper produces


def _load() -> ModuleType:
    name = "lab_capture_review_probe"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _build_install(root: Path, flavors: dict[str, tuple[str, str, bytes]]) -> None:
    """`flavors` maps folder -> (product, version, Config.wtf bytes)."""
    header = "Branch!STRING:0|Active!DEC:1|Version!STRING:0|Product!STRING:0\n"
    rows = "".join(f"us|1|{version}|{product}\n" for product, version, _ in flavors.values())
    _write(root / ".build.info", (header + rows).encode())
    for folder, (product, _version, config) in flavors.items():
        base = root / folder
        _write(base / ".flavor.info", f"Product Flavor!STRING:0\n{product}\n".encode())
        _write(base / "WTF" / "Config.wtf", config)
        character = base / "WTF" / "Account" / ACCOUNT / REALM / CHARACTER
        _write(character / "AddOns.txt", b"Solo: enabled\n")


def _capture(root: Path, out: Path, *extra: str) -> int:
    argv = ["--root", str(root), "--out", str(out), "--platform", "probe", *extra]
    return int(lab_capture.main(argv))


# ─── 1. a stale copy survives a re-run ───────────────────────────────────────


def test_constructed_rerun_does_not_leave_a_copy_it_would_now_refuse(tmp_path: Path) -> None:
    """LAB_PLAN §8: the tool "refuses to emit" what its current map says is identity.

    The owner reviews `incoming/`, sees a guild name, re-runs with
    `--extra-name`. The file that carries the name in a second casing is now
    passed over by the automatic selection, the run exits 0, and the copy
    from the first run is still in `--out`, ready to be moved and committed.
    """
    root = tmp_path / "World of Warcraft"
    _build_install(root, {"flavor_a": ("prod_a", "9.9.9.1", b'SET portal "US"\r\n')})
    saved = root / "flavor_a" / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    body = f'\nRosterGuild = "{GUILD_RETITLED}"\n'.encode()
    _write(saved / "Roster.lua", body)
    out = tmp_path / "incoming"
    dest = out / "probe" / "flavor_a" / "WTF" / "Account" / PSEUDO_ACCOUNT
    dest = dest / "SavedVariables" / "Roster.lua"

    assert _capture(root, out) == 0
    # Positive control: the first run captured the file, and it holds the name.
    assert dest.is_file() and GUILD_RETITLED.encode() in dest.read_bytes()

    code = _capture(root, out, "--extra-name", GUILD)

    # Positive control: told about the name, the scrubber does refuse these bytes.
    assert lab_capture.Identity(extras=[GUILD]).scrub(body).problems

    leftover = dest.is_file() and GUILD_RETITLED.encode() in dest.read_bytes()
    assert not (code == 0 and leftover), (
        "the re-run exited 0 while --out still holds a file this run would refuse; "
        "expected a non-zero exit (for example, refusing to reuse a non-empty "
        "--out/<platform>, or failing on files there that this run did not write)"
    )


# ─── 2. identity CVar lines the `^` anchor cannot see ────────────────────────


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(
            b'\xef\xbb\xbfSET accountName "LEGACYLOGIN"\r\nSET portal "US"\r\n',
            id="constructed-utf8-bom-first-line",
        ),
        pytest.param(
            b'SET portal "US"\rSET accountName "LEGACYLOGIN"\r',
            id="constructed-cr-only-line-endings",
        ),
    ],
)
def test_constructed_identity_cvar_is_blanked_or_refused(config: bytes) -> None:
    """LAB_PLAN §8: CVars known to carry identity are dropped or blanked.

    Boundary input. `LEGACYLOGIN` is a pre-Battle.net login name: not an email,
    not the numbered account folder, so nothing else in the scrubber sees it.
    """
    identity = lab_capture.Identity(accounts=[ACCOUNT])

    # Positive control: the ordinary shape is blanked and reports nothing.
    control = identity.scrub(b'SET accountName "LEGACYLOGIN"\r\n', blank_cvars=True)
    assert control.data == b'SET accountName ""\r\n' and not control.problems

    result = identity.scrub(config, blank_cvars=True)
    assert b"LEGACYLOGIN" not in result.data or result.problems, (
        "the accountName value survived and the file was not refused"
    )


# ─── 3. two flavors, one --kind ──────────────────────────────────────────────


def test_constructed_two_flavors_cannot_share_one_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    _build_install(
        root,
        {
            "flavor_a": ("prod_a", "9.9.9.1", b'SET portal "US"\r\n'),
            "flavor_b": ("prod_b", "1.1.1.2", b'SET portal "EU"\r\n'),
        },
    )
    # Positive control: distinct kinds keep both files.
    apart = tmp_path / "apart"
    assert _capture(root, apart, "--kind", "flavor_a=one", "--kind", "flavor_b=two") == 0
    assert (apart / "probe" / "one" / "WTF" / "Config.wtf").read_bytes() == b'SET portal "US"\r\n'
    assert (apart / "probe" / "two" / "WTF" / "Config.wtf").read_bytes() == b'SET portal "EU"\r\n'
    capsys.readouterr()

    out = tmp_path / "together"
    code = _capture(root, out, "--kind", "flavor_a=same", "--kind", "flavor_b=same")
    rows = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("| `probe/same/WTF/Config.wtf`")
    ]
    assert code != 0 or len(rows) <= 1, (
        "two source files were written to one destination (the second silently "
        "replaced the first) and two provenance rows name the same file; exit was 0"
    )


# ─── 4. NFD folder name, NFC contents ────────────────────────────────────────


def test_constructed_nfd_folder_name_still_scrubs_nfc_contents() -> None:
    """HFS+ hands directory names back decomposed; the client writes NFC into files."""
    nfc = "Thrâllmar"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc.encode() != nfd.encode()
    contents = f'["{nfc} - {REALM}"] = "Default",\n'.encode()

    # Positive control: the same name in the same form is replaced.
    same_form = lab_capture.Identity(characters=[nfc]).scrub(contents)
    assert nfc.encode() not in same_form.data and not same_form.problems

    result = lab_capture.Identity(characters=[nfd]).scrub(contents)
    assert nfc.encode() not in result.data or result.problems, (
        "a character folder listed in NFD left the NFC spelling in the file, unreported"
    )
