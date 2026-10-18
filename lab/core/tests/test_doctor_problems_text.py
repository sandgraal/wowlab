"""`doctor` prints its `problems` in text, and a first run names a linked
flavor folder beside a real one (M13-56).

The install is `test_not_taken`'s: the captured tree (`fixtures/macos/`)
copied into `tmp_path`, its flavor folder named as its provenance row says,
`--root` pointing at the copy, the user data directory redirected and no
client in the process table. Nothing reads or writes a real install.

Constructed (L8, boundary), as there: the link `_retail_` (a symlink to a
copy of the captured flavor folder outside the install), the empty folder
`_empty_`, and, here, the real flavor folder removed from the copy after a
snapshot of it was taken, so a later run (not a first run) finds none.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from test_not_taken import (
    EMPTY,
    FLAVOR,
    LINKED,
    _install,
    _run,
    idle,  # noqa: F401  (autouse: no client in the process table)
    userdata,  # noqa: F401  (autouse: the store lives in tmp_path)
)

from wowlab_core import install


def _no_readable(root: Path, target: Path, *, empty: bool) -> str:
    """The refusal's words with each reason (`cli._no_flavors`)."""
    taken = f"{EMPTY} (no regular .flavor.info file), " if empty else ""
    return (
        f"{root} {install.NO_READABLE_FLAVOR}; not taken: {taken}"
        f"{LINKED} (link to {target}, not followed); {install.NOT_TAKEN_LINK}"
    )


def _problems_block(problems: list[str]) -> str:
    return "Problems:\n" + "".join(f"  {p}\n" for p in problems)


def _link_line(target: Path) -> str:
    return (
        f"{LINKED} is a link to {target}, not followed, so its WTF (settings and "
        "SavedVariables), Interface and Fonts are not snapshotted: "
        f"{install.NOT_TAKEN_LINK}. Back up the folder it points to yourself.\n"
    )


# ─── (1) problems in text ────────────────────────────────────────────────────


def test_redact_prints_why_it_exits_1_as_the_json_does_constructed(tmp_path: Path) -> None:
    """`--redact` skips the first run, so before M13-56 it exited 1 with no
    line saying why. The text now lists `problems` exactly as `--json` does."""
    root, _ = _install(tmp_path, real=False, empty=True)

    text = _run("doctor", "--offline", "--redact", "--root", str(root), code=1).stdout
    data = json.loads(
        _run("doctor", "--offline", "--redact", "--json", "--root", str(root), code=1).stdout
    )

    assert len(data["problems"]) == 1
    assert "; not taken: " in data["problems"][0]
    assert text.endswith(_problems_block(data["problems"]))


def test_a_later_run_with_no_readable_flavor_says_which_folders_and_why_constructed(
    tmp_path: Path,
) -> None:
    """Not a first run (a snapshot of this install is in the store), so only
    `problems` can say why it exits 1: each folder not taken, with its
    reason, in the text and in `--json` alike."""
    root, target = _install(tmp_path, empty=True)
    _run("snap", "create", "--flavor", FLAVOR, "--root", str(root))
    shutil.rmtree(root / FLAVOR)

    text = _run("doctor", "--offline", "--root", str(root), code=1).stdout
    data = json.loads(_run("doctor", "--offline", "--json", "--root", str(root), code=1).stdout)

    assert data["first_run"] is None
    assert "First run" not in text
    assert data["problems"] == [_no_readable(root, target, empty=True)]
    assert text.endswith(_problems_block(data["problems"]))


def test_a_first_run_with_only_a_link_lists_the_problem_too_constructed(tmp_path: Path) -> None:
    root, target = _install(tmp_path, real=False)

    text = _run("doctor", "--offline", "--root", str(root), code=1).stdout
    data = json.loads(_run("doctor", "--offline", "--json", "--root", str(root), code=1).stdout)

    assert data["problems"] == [_no_readable(root, target, empty=False)]
    assert _problems_block(data["problems"]) in text


def test_with_nothing_not_taken_the_problem_keeps_its_old_words(tmp_path: Path) -> None:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(Path(__file__).parent / "fixtures" / "macos" / ".build.info", root / ".build.info")

    text = _run("doctor", "--offline", "--redact", "--root", str(root), code=1).stdout
    data = json.loads(
        _run("doctor", "--offline", "--redact", "--json", "--root", str(root), code=1).stdout
    )

    assert [p.endswith(" has no flavor folders") for p in data["problems"]] == [True]
    assert text.endswith(_problems_block(data["problems"]))


def test_no_problems_prints_no_problems_block(tmp_path: Path) -> None:
    root, _ = _install(tmp_path)
    (root / LINKED).unlink()

    text = _run("doctor", "--offline", "--root", str(root)).stdout

    assert "Problems:" not in text


# ─── (2) a first run with a real flavor beside a linked one ──────────────────


def test_a_first_run_names_a_linked_folder_beside_a_real_flavor_constructed(
    tmp_path: Path,
) -> None:
    """The line the link-only first run gives (M13-45), under the flavor
    list, once; a folder not taken for another reason is not repeated."""
    root, target = _install(tmp_path, empty=True)

    text = _run("doctor", "--offline", "--root", str(root)).stdout

    scan = text.index(f"\nFlavor {FLAVOR} (")
    line = text.index(f"\n{_link_line(target)}")
    offer = text.index("\nTo take a first snapshot: ")
    assert scan < line < offer
    assert f"  {EMPTY}: " not in text[scan:]
    assert text.count(_link_line(target)) == 1
    assert "Problems:" not in text


def test_a_first_run_with_no_link_beside_its_flavor_says_nothing_of_links(
    tmp_path: Path,
) -> None:
    root, _ = _install(tmp_path, empty=True)
    (root / LINKED).unlink()

    text = _run("doctor", "--offline", "--root", str(root)).stdout

    assert " is a link to " not in text
    assert "Back up the folder it points to yourself" not in text
