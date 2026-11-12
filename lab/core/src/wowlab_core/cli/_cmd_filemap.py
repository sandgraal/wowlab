"""`wowlab tree`, `wowlab explain` and `wowlab filemap gaps` (M16-111)."""

import os
import re
import shlex
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Annotated, Literal, NamedTuple

import typer
from pydantic import Field

from wowlab_core import (
    combatlog,
    filemap,
    gaps,
    install,
    layout,
    longpath,
    pathfacts,
    snapshot,
)
from wowlab_core.cli._cmd_log import (
    _logs_dir,
)
from wowlab_core.cli._common import (
    _KIND_WORDS,
    EXIT_ERROR,
    EXIT_USAGE,
    CliError,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    _bytes,
    _discover,
    _emit,
    _given_flavor,
    _handled,
    _not_taken_named,
    _note,
    _open,
    _Out,
    _player_copy,
    _powershell_join,
    _pre_write_ids,
    _reader_words,
    _say,
    _select_flavor,
    _shell,
    app,
    filemap_app,
)
from wowlab_core.filemap import FileMapEntry
from wowlab_core.verdict import (
    WOWLAB_TEMP_ROW,
    Verdict,
    gate_refuses_stat,
    tier_words,
    verdict_of,
)

# ─── tree and explain ────────────────────────────────────────────────────────


class PlainText(_Out):
    """A file-map row's `what` and `written_by` as plain text for a reader
    who does not see Markdown (M13-25): code spans lose their backticks,
    `**[verify]**` reads "(not yet confirmed)", other bold markers go."""

    what: str
    written_by: str


_VERIFY_MARK = re.compile(r"\*\*\[verify(?: ([^\]]*))?\]\*\*")


def _player_text(markdown: str) -> str:
    """A file-map cell without Markdown, for `PlainText`."""
    text = _VERIFY_MARK.sub(
        lambda m: f"({m.group(1)} not yet confirmed)" if m.group(1) else "(not yet confirmed)",
        markdown,
    )
    return text.replace("**", "").replace("`", "")


def _plain_text(entry: FileMapEntry) -> PlainText:
    return PlainText(what=_player_text(entry.what), written_by=_player_text(entry.written_by))


class Explained(_Out):
    """A file-map entry, as `explain` and `tree --explain` show it."""

    entry_id: str
    what: str  # the player's text (M16-17)
    notes: str  # the spec's notes on the row (M16-17); "—" when it has none
    written_by: str
    edit: str
    tier: str
    module: str
    verdict: Verdict  # M13-21: the Edit cell as a level and in words
    plain: PlainText  # M13-25: `what` and `written_by` without Markdown


class TreeItem(_Out):
    path: str  # relative to `base`: the flavor folder, or with --install perhaps the install root
    kind: Literal["file", "folder", "symlink"]
    size: int | None
    note: str | None
    # M13-21, amended 2026-10-15: always present; "unclassified" with no file-map row
    verdict: Verdict
    explained: Explained | None
    # M13-22: what `path` is relative to; "" with base "flavor" is the flavor folder itself
    base: Literal["root", "flavor"]
    flavor_folder: str | None  # set when `base` is "flavor"


class TreeReport(_Out):
    """`wowlab tree --json`.

    `ancestors` (M13-25, JSON only): every folder above a listed item that
    is not listed itself, such as `Interface/AddOns` or `WTF/Account`, with
    its own verdict, so a viewer drawing the items as a tree never draws a
    folder without one. `flavor_folder_verdict`: the verdict of the flavor
    folder itself."""

    install_root: str
    flavor_folder: str
    items: list[TreeItem]
    truncated: bool
    errors: list[str]
    ancestors: list[TreeItem]
    flavor_folder_verdict: Verdict


class InstallTreeReport(_Out):
    """`wowlab tree --install --json` (M13-22): the install root's own
    entries (one level, the file map's `root` section), each flavor folder
    as an item (`base` "flavor", `path` ""), then each flavor's inventory as
    `tree` lists it."""

    install_root: str
    flavor_folders: list[str]  # as discovery reported them
    items: list[TreeItem]  # the root's entries in name order, then each flavor's
    truncated: bool
    errors: list[str]  # paths relative to the install root
    # M13-33, JSON only: each flavor's folders above a listed item that are
    # not listed themselves, as `TreeReport.ancestors` has them, flavor by
    # flavor in `flavor_folders` order.
    ancestors: list[TreeItem] = []  # noqa: RUF012 - a pydantic field (`_Out`)


def _explained(entry: FileMapEntry, verdict: Verdict) -> Explained:
    return Explained(
        entry_id=entry.id,
        what=entry.what,
        notes=entry.notes,
        written_by=entry.written_by,
        edit=entry.edit,
        tier=entry.tier,
        module=entry.module,
        verdict=verdict,
        plain=_plain_text(entry),
    )


@dataclass(frozen=True)
class _Reparse:
    """Where a flavor's paths meet a reparse point that is no link (M13-33),
    so `tree` judges them as `explain` does. `whole`: the flavor folder
    itself is one, so every path in it is a placeholder. `placeholders`:
    paths relative to the flavor folder whose own `lstat` the write gate
    refuses. A link (`layout.is_link`) is never among them: the walk lists
    it in `Inventory.symlinks` and never goes into it (M13-35)."""

    whole: bool = False
    placeholders: frozenset[str] = frozenset()

    @classmethod
    def of(cls, inv: layout.Inventory, *, whole: bool) -> "_Reparse":
        return cls(whole=whole, placeholders=frozenset(r.path for r in inv.reparse_points))

    def judge(self, path: str) -> bool:
        """Whether `path` is a placeholder: it, or a folder above it, is one."""
        parts = path.split("/") if path else []
        above = {"/".join(parts[:n]) for n in range(1, len(parts) + 1)}
        return self.whole or not above.isdisjoint(self.placeholders)


def _flavor_reparse(lay: layout.Layout) -> tuple[bool, bool]:
    """`(link, placeholder)` for the flavor folder itself, as `explain`'s
    component walk judges it: a link by `gaps.is_link` (M13-35), else a
    reparse point the write gate refuses that is no link (M13-32)."""
    try:
        st = longpath.lstat(lay.flavor_path)
    except OSError:
        return False, False
    if gaps.is_link(lay.flavor_path, st):
        return True, False
    return False, gate_refuses_stat(st)


def _tree_items(
    lay: layout.Layout, inv: layout.Inventory, reparse: _Reparse | None = None
) -> dict[str, TreeItem]:
    flavor_folder = lay.flavor_folder
    marks = reparse if reparse is not None else _Reparse.of(inv, whole=False)

    items: dict[str, TreeItem] = {}
    # M13-50: the folders under WTF/Account/ the walk did not take as accounts.
    not_accounts = [p.rsplit("/", 1)[-1] for p in inv.not_accounts]

    def add(
        path: str, kind: Literal["file", "folder", "symlink"], size: int | None, note: str | None
    ) -> None:
        if path and path not in items:
            found = lay.classify(path, is_dir=kind == "folder" or None, not_accounts=not_accounts)
            entry = found.entry if isinstance(found, layout.Classified) else None
            items[path] = TreeItem(
                path=path,
                kind=kind,
                size=size,
                note=note,
                verdict=verdict_of(
                    entry,
                    found.base,
                    found.path,
                    folder=kind == "folder",
                    link=kind == "symlink",
                    placeholder=marks.judge(path),
                ),
                explained=None,
                base="flavor",
                flavor_folder=flavor_folder,
            )

    try:
        top = sorted(longpath.entries(lay.flavor_path), key=lambda e: e.name)
    except OSError:
        top = []
    for e in top:
        try:
            st = layout.lstat_entry(e)
        except OSError:
            # Judged from the listing alone, as before M13-35; the walk
            # names the error.
            if not e.is_symlink():
                add(e.name, "folder" if e.is_dir(follow_symlinks=False) else "file", None, None)
            continue
        if gaps.is_link(lay.flavor_path / e.name, st):
            continue  # the walk reports it, with its target, below (M13-35)
        add(e.name, "folder" if stat.S_ISDIR(st.st_mode) else "file", None, None)
    for acct in inv.accounts:
        add(acct.path, "folder", None, "account folder")
        for realm in acct.realms:
            note = (
                "numeric folder (not a realm name)" if realm.kind == "numeric" else "realm folder"
            )
            add(realm.path, "folder", None, note)
            for c in realm.characters:
                add(c.path, "folder", None, "character folder")
    for path in inv.not_accounts:
        add(path, "folder", None, layout.not_account_note(path.rsplit("/", 1)[-1]))
    for f in inv.wtf_files:
        add(f.path, "file", f.size, None)
    for sv in inv.saved_variables:
        who = "Blizzard UI" if sv.blizzard else f"addon {sv.addon}"
        what = "previous write of " if sv.backup else ""
        add(sv.path, "file", sv.size, f"{what}{sv.scope} SavedVariables of {who}")
    for addon in inv.addons:
        add(addon.path, "folder", None, f"addon; {_SELECTION_WORDS[addon.selection]}")
        for toc in addon.tocs:
            add(f"{addon.path}/{toc.file}", "file", toc.size, toc.error)
    for o in inv.other.overrides:
        add(o.path, "file", o.size, f"{o.area} override")
    for area in inv.other.areas:
        if area.present and area.path is not None and not area.symlink:
            add(
                area.path,
                "folder",
                area.bytes,
                f"{area.files} files in {area.folders} folders, not listed one by one",
            )
    for link in inv.symlinks:
        stands_for = _link_stands_for(
            lay.classify(link.path, is_dir=False, not_accounts=not_accounts),
            lay.classify(link.path, is_dir=True, not_accounts=not_accounts),
        )
        add(link.path, "symlink", None, _link_note(link.target, link.inside_install, stands_for))
    for meta in inv.os_metadata:
        add(meta, "file", None, "operating-system folder metadata")
    return items


def _link_note(target: str | None, inside: bool | None, stands_for: str = "folder") -> str:
    """`tree`'s note for a link, at the install root and in a flavor folder
    alike (M13-35). `inside` None: where it points is unknown, as for a WCI
    link or a WSL symlink, whose link text cannot be read. It ends in the
    readers' words for a link (M16-80), as `labaddon.NOT_FOLLOWED` and
    `labaddon.LINKED_FILE` say it: `stands_for` is "folder", "file" or
    "one" (`_link_stands_for`)."""
    to = f" to {target}" if target is not None else ""
    if inside is None:
        where = "where it points is unknown"
    else:
        where = f"{'inside' if inside else 'outside'} the install"
    return f"link{to} ({where}), not followed; {snapshot.not_read_through(stands_for)}"


_FLAVOR_LINK_NOTE = (
    "{} is a link; wowlab does not read through links, so it reads this only when the "
    "real folder is there. Nothing in it is listed."
)
"""`tree`'s stderr line for a flavor folder that is a link (M13-35), in the
readers' words for a linked folder (M16-80: `snapshot.not_read_through`).
A literal, as every `_note` message is; a test holds it to those words."""


def _link_stands_for(
    as_file: layout.Classified | layout.Unclassified,
    as_folder: layout.Classified | layout.Unclassified,
) -> str:
    """What a link stands in place of, for `_link_note` (M16-80), from the
    file map alone, as the link is never followed: `as_file` and `as_folder`
    are its place classified as each kind. "file" where only a file there
    gets a row (a linked `WTF/Config.wtf`), "folder" where only a folder
    does, or where one row takes both and the place is the folder that row's
    subtree hangs from (`Fonts`, `Interface/AddOns/<addon>`). "file", too,
    where a file there has its own row and a folder there only the general
    row of a folder above it (`Logs/WoWCombatLog.txt`: `combat-log` as a
    file, `logs-other`, which hangs from `Logs`, as a folder; likewise
    `Logs/*.log`, and `Cache/ADB/<locale>/DBCache.bin` and
    `Cache/WDB/<locale>/*.wdb` under `cache-other`, which hangs from
    `Cache`; M16-91). "file", too, where a file there has a row that spells
    out its name and a folder there only a row that takes a folder of any
    name at this one place (`WTF/Account/<acct>/macros-cache.txt`:
    `account-macros-cache` as a file, `realm-folder` as a folder; M16-95).
    Otherwise, one row for both kinds below that folder (a font file, an
    addon's `.toc`), other different rows, or none: "one", which says
    neither."""
    file_row = as_file.entry if isinstance(as_file, layout.Classified) else None
    folder_row = as_folder.entry if isinstance(as_folder, layout.Classified) else None
    if file_row is not None and folder_row is None:
        return "file"
    if file_row is not None and folder_row is not None and file_row.id != folder_row.id:
        parts = as_folder.path.split("/")
        if not folder_row.heads_a_subtree(parts) and any(
            folder_row.heads_a_subtree(parts[:n]) for n in range(1, len(parts))
        ):
            return "file"
        if file_row.names_the_file(parts) and folder_row.takes_any_name_here(parts):
            return "file"
    if folder_row is not None and file_row is None:
        return "folder"
    if (
        file_row is not None
        and folder_row is not None
        and file_row.id == folder_row.id
        and folder_row.heads_a_subtree(as_folder.path.split("/"))
    ):
        return "folder"
    return "one"


_SELECTION_WORDS = {
    "single": "one TOC",
    "depends_on_game_type": "several TOCs; which one loads depends on the game type",
    "no_toc": "no TOC named after the folder",
}


def _in_scope(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")


def _folder_verdict(lay: layout.Layout, path: str, reparse: _Reparse | None = None) -> Verdict:
    found = lay.classify(path, is_dir=True)
    entry = found.entry if isinstance(found, layout.Classified) else None
    placeholder = reparse.judge(path) if reparse is not None else False
    return verdict_of(entry, found.base, found.path, folder=True, placeholder=placeholder)


def _link_verdict(lay: layout.Layout, path: str) -> Verdict:
    """The verdict of a link in a flavor folder, as `tree` gives a listed
    link and `explain` gives it: classified by its own name, never followed."""
    found = lay.classify(path)
    entry = found.entry if isinstance(found, layout.Classified) else None
    return verdict_of(entry, found.base, found.path, link=True)


def _ancestors(
    lay: layout.Layout, items: list[TreeItem], prefix: str, reparse: _Reparse | None = None
) -> list[TreeItem]:
    """The folders above `items` that are not items themselves, in scope,
    sorted, each with its verdict. They are read from the item paths, never
    from the disk: a folder above a listed path is a folder. `reparse`
    (M13-33): the reparse points the inventory walk met."""
    listed = {item.path for item in items}
    above: set[str] = set()
    for item in items:
        parts = item.path.split("/")
        for n in range(1, len(parts)):
            path = "/".join(parts[:n])
            if path not in listed and _in_scope(path, prefix):
                above.add(path)
    return [
        TreeItem(
            path=path,
            kind="folder",
            size=None,
            note=None,
            verdict=_folder_verdict(lay, path, reparse),
            explained=None,
            base="flavor",
            flavor_folder=lay.flavor_folder,
        )
        for path in sorted(above)
    ]


@app.command(short_help="What a flavor folder holds: settings, addons, saved data.")
@_handled
def tree(
    path: Annotated[
        str | None,
        typer.Argument(
            help="Only this path under the flavor folder (with --install, under the install root)."
        ),
    ] = None,
    explain: Annotated[
        bool, typer.Option("--explain", help="Add each path's file-map entry.")
    ] = False,
    whole_install: Annotated[
        bool,
        typer.Option(
            "--install",
            help="The whole install: the install root's own entries and every flavor folder.",
        ),
    ] = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Inventory of a flavor folder: WTF files, SavedVariables, addons, overrides,
    areas (counted, not listed). With --install, the install
    root's own entries (one level) and every flavor folder's inventory; each
    item names its base and flavor folder.

    \f
    JSON: TreeReport; with --install, InstallTreeReport."""
    if whole_install:
        if flavor is not None:
            raise CliError("--install covers every flavor folder; leave out --flavor", EXIT_USAGE)
        _tree_install(path, explain, root, json_out)
        return
    inst, chosen, lay = _open(root, flavor)
    prefix = _tree_prefix(path, lay.flavor_path)
    flavor_link, flavor_placeholder = _flavor_reparse(lay)
    items: list[TreeItem] = []
    ancestors: list[TreeItem] = []
    errors: list[str] = []
    truncated = False
    if flavor_link:
        # M13-35: the flavor folder is a link (a WCI link, say, which
        # discovery takes as a folder): listed as one, never walked.
        folder_verdict = _link_verdict(lay, "")
    else:
        inv = lay.inventory()
        reparse = _Reparse.of(inv, whole=flavor_placeholder)
        items = [
            i for p, i in sorted(_tree_items(lay, inv, reparse).items()) if _in_scope(p, prefix)
        ]
        ancestors = _ancestors(lay, items, prefix, reparse)
        if explain:
            items = _with_explained(lay, items)
            ancestors = _with_explained(lay, ancestors)
        errors = [f"{e.path}: {e.error}" for e in inv.errors]
        truncated = inv.truncated
        folder_verdict = _folder_verdict(lay, "", reparse)
    report = TreeReport(
        install_root=str(inst.root),
        flavor_folder=chosen.folder,
        items=items,
        truncated=truncated,
        errors=errors,
        ancestors=ancestors,
        flavor_folder_verdict=folder_verdict,
    )
    if json_out:
        _emit(report)
        return
    _say(f"{chosen.folder} in {inst.root}")
    if flavor_link:
        _note(_FLAVOR_LINK_NOTE, chosen.folder)
    for item in items:
        _say_tree_item(item.path, item, explain)
    if truncated:
        _note("The walk hit a bound; the listing is incomplete.")
    for err in errors:
        _note("could not read {}", err)


def _tree_prefix(path: str | None, base: Path) -> str:
    """`tree`'s PATH argument, relative to `base` and `/`-separated."""
    if not path:
        return ""
    path = _plain_arg(path)  # a `\\?\` spelling taken plainly, as `explain` does (M13-58)
    given = Path(path)
    if given.is_absolute():
        try:
            prefix = given.relative_to(base).as_posix()
        except ValueError:
            raise CliError(f"{path} is not inside {base}", EXIT_USAGE) from None
    else:
        prefix = given.as_posix().strip("/")
    return "" if prefix == "." else prefix


def _with_explained(lay: layout.Layout, items: list[TreeItem]) -> list[TreeItem]:
    explained = []
    for item in items:
        found = lay.classify(item.path, is_dir=item.kind == "folder" or None)
        if isinstance(found, layout.Classified):
            item = item.model_copy(update={"explained": _explained(found.entry, item.verdict)})
        explained.append(item)
    return explained


def _say_tree_item(shown: str, item: TreeItem, explain: bool) -> None:
    slash = "/" if item.kind == "folder" else ""
    size = f"  {_bytes(item.size)}" if item.size is not None and item.kind == "file" else ""
    note = f"  ({item.note})" if item.note else ""
    _say(f"  {shown}{slash}{size}{note}")
    if explain:
        if item.explained is None:
            _say("      no file-map row")
        else:
            e = item.explained
            _say(f"      [{e.entry_id}] {_player_copy(_plain(e.what))}")
            _say(f"      written by: {_player_copy(_plain(e.written_by))}")
            _say(f"      edit: {e.verdict.words}")


def _install_path(item: TreeItem) -> str:
    """An item's path relative to the install root."""
    if item.base == "root" or item.flavor_folder is None:
        return item.path
    return f"{item.flavor_folder}/{item.path}" if item.path else item.flavor_folder


def _root_items(inst: install.Install, errors: list[str]) -> list[TreeItem]:
    """The install root's own entries, one level, in name order. A flavor
    folder is an item of base "flavor" and path "" (L6: by discovery's
    name only); a link is named, never followed. A reparse point that is no
    link is judged a placeholder, as `explain` judges it (M13-33)."""
    flavors = {f.folder for f in inst.flavors}
    root = Path(inst.root)
    try:
        real_root: Path | None = longpath.resolve(root)
    except (OSError, RuntimeError):
        real_root = None
    try:
        entries = sorted(longpath.entries(root), key=lambda e: e.name)
    except OSError as exc:
        errors.append(f".: {exc.strerror or exc}")
        return []
    items = []
    for e in entries:
        try:
            st = gaps.lstat_entry(e)
        except OSError as exc:
            errors.append(f"{e.name}: {exc.strerror or exc}")
            continue
        link = gaps.is_link(root / e.name, st)
        placeholder = not link and gate_refuses_stat(st)
        folder = not link and stat.S_ISDIR(st.st_mode)
        kind: Literal["file", "folder", "symlink"] = (
            "symlink" if link else "folder" if folder else "file"
        )
        found = layout.classify(e.name, inst, is_dir=folder or None)
        entry = found.entry if isinstance(found, layout.Classified) else None
        note: str | None = None
        if link:
            stands_for = (
                "folder"
                if e.name in flavors
                else _link_stands_for(
                    layout.classify(e.name, inst, is_dir=False),
                    layout.classify(e.name, inst, is_dir=True),
                )
            )
            note = _link_note(*layout.link_facts(root / e.name, real_root), stands_for)
            if e.name in flavors:
                # M13-35: as `tree` says it for this flavor folder alone.
                note = f"flavor folder; {note}; nothing in it is listed"
            elif e.name in inst.other_dirs:
                note += f"; {gaps.NOT_A_FLAVOR_LINK}"
        elif folder and e.name in flavors:
            note = "flavor folder"
        elif folder and e.name in inst.other_dirs:
            note = install.folder_note(inst, e.name)  # M13-45: its own reason
        items.append(
            TreeItem(
                path=found.path,
                kind=kind,
                size=st.st_size if kind == "file" else None,
                note=note,
                verdict=verdict_of(
                    entry,
                    found.base,
                    found.path,
                    folder=folder,
                    link=link,
                    placeholder=placeholder,
                ),
                explained=None,
                base="flavor" if found.base == "flavor" else "root",
                flavor_folder=found.flavor_folder,
            )
        )
    return items


def _tree_install(path: str | None, explain: bool, root: Path | None, json_out: bool) -> None:
    inst, _ = _discover(root)
    prefix = _tree_prefix(path, Path(inst.root))
    errors: list[str] = []
    items = _root_items(inst, errors)
    if explain:
        explained = []
        for item in items:
            found = layout.classify(_install_path(item), inst, is_dir=item.kind == "folder" or None)
            if isinstance(found, layout.Classified):
                item = item.model_copy(update={"explained": _explained(found.entry, item.verdict)})
            explained.append(item)
        items = explained
    placeholder_flavors = {
        i.flavor_folder
        for i in items
        if i.base == "flavor" and not i.path and i.verdict.placeholder
    }
    # M13-35: a flavor folder that is a link is listed above, never walked.
    link_flavors = {i.flavor_folder for i in items if i.base == "flavor" and i.kind == "symlink"}
    truncated = False
    ancestors: list[TreeItem] = []
    for f in inst.flavors:
        if f.folder in link_flavors:
            continue
        lay = layout.Layout.for_flavor(f, inst.root)
        inv = lay.inventory()
        reparse = _Reparse.of(inv, whole=f.folder in placeholder_flavors)
        listed = [i for _, i in sorted(_tree_items(lay, inv, reparse).items())]
        in_scope = [i for i in listed if _in_scope(_install_path(i), prefix)]
        above = [
            i for i in _ancestors(lay, in_scope, "", reparse) if _in_scope(_install_path(i), prefix)
        ]
        ancestors.extend(_with_explained(lay, above) if explain else above)
        items.extend(_with_explained(lay, listed) if explain else listed)
        truncated = truncated or inv.truncated
        errors.extend(f"{f.folder}/{e.path}: {e.error}" for e in inv.errors)
    items = [i for i in items if _in_scope(_install_path(i), prefix)]
    report = InstallTreeReport(
        install_root=str(inst.root),
        flavor_folders=[f.folder for f in inst.flavors],
        items=items,
        truncated=truncated,
        errors=errors,
        ancestors=ancestors,
    )
    if json_out:
        _emit(report)
        return
    _say(f"Install root {inst.root}")
    for item in items:
        if item.base == "root" or not item.path:
            _say_tree_item(_install_path(item), item, explain)
    for folder in report.flavor_folders:
        inside = [i for i in items if i.flavor_folder == folder and i.path]
        if inside:
            _say(f"{folder} in {inst.root}")
        for item in inside:
            _say_tree_item(item.path, item, explain)
    if truncated:
        _note("The walk hit a bound; the listing is incomplete.")
    for err in errors:
        _note("could not read {}", err)


# ─── filemap gaps ────────────────────────────────────────────────────────────


@filemap_app.command("gaps", short_help="Paths no file-map row describes, grouped and ranked.")
@_handled
def filemap_gaps(root: RootOpt = None, json_out: JsonOpt = False) -> None:
    """Where the file map is thin: paths with no file-map row, and paths only
    a catch-all row ("not described here yet") matches. They are grouped by
    the parent folder's row and their own name, and ranked by file count,
    then bytes. The walk covers the install root and every flavor folder,
    reads only, never follows a link, is bounded, and names what it could
    not read. A folder a row covers whole (`Data/`, each
    `Interface/AddOns/<Addon>/`, the rest of `Interface/`) is labelled by that
    row and not looked inside, so an addon unpacked one folder too deep, or
    stray files under Interface/, are not reported; the folders not entered
    are listed.

    \f
    JSON: gaps.GapReport."""
    inst, _ = _discover(root)
    report = gaps.find_gaps(inst)
    if json_out:
        _emit(report)
    else:
        _print_gaps(report)
    if report.truncated:
        _note("The walk hit a bound; there may be gaps it did not reach.")
    for err in report.errors:
        _note("could not read {}: {}", err.path, err.error)


def _count(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def _group_counts(group: gaps.GapGroup) -> str:
    if group.kind == "file":
        parts = [_bytes(group.bytes)]
    elif group.kind == "link":
        parts = ["link, not followed"]
    elif group.kind == "other":
        parts = ["not a file, folder or link; never opened"]
    else:
        parts = [_count(group.files, "file"), _bytes(group.bytes)]
        if group.folders:
            parts.append(_count(group.folders, "folder"))
        if group.links:
            parts.append(_count(group.links, "link") + " not followed")
        if group.other:
            parts.append(f"{group.other:,} other")
    if group.places > 1:
        parts.append(f"in {group.places:,} places")
    if group.entry_id:
        parts.append(f"row {group.entry_id}")
    if group.note:
        parts.append(group.note)
    return ", ".join(parts)


def _say_gap_groups(groups: list[gaps.GapGroup]) -> None:
    """One line per group: the parent's row and the name, never the parent's
    own path (an account, realm or character folder name)."""
    for group in groups:
        parent = f"[{group.parent_entry_id}]" if group.parent_entry_id else "(install root)"
        slash = "/" if group.kind == "folder" else ""
        _say(f"  {parent} {group.name}{slash}  {_group_counts(group)}")
        for example in _gap_examples(group):
            _say(f"      {example}")


def _gap_examples(group: gaps.GapGroup) -> list[str]:
    """Up to `gaps.EXAMPLES` files under a folder group, each relative to
    its place's parent folder, so the line starts at the place's own name
    and never shows an account, realm or character folder."""
    out: list[str] = []
    for place in group.gaps:
        cut = len(place.parent) + 1 if place.parent else 0
        for example in place.examples:
            shown = example[cut:]
            if shown not in out:
                out.append(shown)
            if len(out) == gaps.EXAMPLES:
                return out
    return out


def _print_gaps(report: gaps.GapReport) -> None:
    _say(f"File-map gaps in {report.install_root}")
    if report.groups:
        _say(
            f"Paths with no file-map row: {_count(report.files, 'file')}, "
            f"{_bytes(report.bytes)}, in {_count(len(report.groups), 'group')}"
        )
        _say_gap_groups(report.groups)
    else:
        none = "Paths with no file-map row: none; every path the walk reached has a row"
        if report.links:
            none += (
                f"; {_count(len(report.links), 'link')} met and not followed (listed below), "
                "so what they point at, a linked WTF/ for one, was not checked"
            )
        _say(none)
    if report.catch_all:
        _say(
            f"Described only by a catch-all row: {_count(report.catch_all_files, 'file')}, "
            f"{_bytes(report.catch_all_bytes)}"
        )
        _say_gap_groups(report.catch_all)
    if report.not_entered:
        _say("Not entered, labelled by a row that covers everything inside:")
        by_row: dict[str, list[gaps.NotEntered]] = {}
        for item in report.not_entered:
            by_row.setdefault(item.entry_id, []).append(item)
        for entry_id, items in by_row.items():
            if len(items) == 1:
                where = "/".join(p for p in (items[0].flavor_folder, items[0].path) if p)
                _say(f"  [{entry_id}] {where}/")
            else:
                _say(f"  [{entry_id}] {_count(len(items), 'folder')}")
    if report.links:
        _say("Links met and not followed:")
        for link in report.links:
            _say(f"  {link}")


def _plain(markdown: str) -> str:
    """File-map cells are Markdown; drop the bold markers for a terminal."""
    return markdown.replace("**", "")


class ExplainView(_Out):
    """An existing read command that decodes this file, or lists what it is
    part of (M13-23).

    `command` is the leaf's path in the command tree and `params` its Click
    parameter names with their values, the shape `wowlab ui` runs; `argv` is
    the same run as `wowlab` arguments. Neither carries `--json`: a caller
    adds it. `does` says what the command shows. No view names a command
    that declares `--yes`, `log tail --follow` or `looks page --out`."""

    command: list[str]
    params: dict[str, str]
    argv: list[str]
    does: str


class ExplainHint(_Out):
    """Command text to copy (M13-23); `explain` never runs it. `argv` is the
    command's arguments after `wowlab`; `command` is the whole line quoted
    for `shell`, the default shell of this machine: `posix` (`shlex.join`,
    for sh, bash and zsh) or `powershell` (single quotes, a quote doubled;
    Windows Terminal's default; not cmd.exe). A copy button copies
    `command` exactly, already quoted for `shell`, and never rebuilds the
    line from `argv` (amended 2026-10-16, M13-27). Every path in it is
    inside the install. `shell_label` (M13-27): which shells take the line,
    in a player's words, for a viewer to show beside it."""

    command: str
    argv: list[str]
    shell: Literal["posix", "powershell"]
    does: str
    shell_label: str = ""


# M13-27: the shells each quoting suits, in a player's words.
_SHELL_LABELS = {
    "posix": "for sh, bash or zsh",
    "powershell": "for PowerShell (not Command Prompt)",
}


class ExplainCandidate(_Out):
    """M16-18: one path, or one file-map row, a name `explain` did not find
    as typed may mean."""

    # A file or folder on disk, relative to its flavor folder; None for a
    # row that names the name but matches no path found.
    path: str | None
    flavor_folder: str | None
    entry_id: str | None  # the row the path has, or the row itself
    doc_path: str | None  # that row's path pattern, as the file map writes it


# The most candidates `explain` prints; `--json` has them all.
_CANDIDATES_SHOWN = 20
# At most this many candidates are each explained in full (M16-18).
_CANDIDATES_EXPLAINED = 3


class ExplainReport(_Out):
    """`wowlab explain --json`."""

    query: str
    result: Annotated[layout.Classified | layout.Unclassified, Field(discriminator="status")]
    verdict: Verdict  # M13-21; level "unclassified" when there is no file-map row
    # M13-23. None: absent, a link or inside one, or not inside the install.
    stat: pathfacts.PathStat | None
    # M13-23. None: not a file in a flavor folder (a folder, a link, an
    # install-root path); else what the snapshots say about it.
    snapshot: pathfacts.SnapshotFacts | None
    views: list[ExplainView]  # M13-23; [] for a link or a path with no file-map row
    # M13-23: why a classified file has no view (no command reads it, or
    # `log tail` reads another log); None otherwise.
    views_note: str | None
    hints: list[ExplainHint]  # M13-23
    plain: PlainText | None = None  # M13-25: the row's text without Markdown
    # M13-29: nothing is on disk at the path under any reading of it (for a
    # relative path: at the install root and in every flavor folder); never
    # True behind a link. `stat` is None then too.
    absent: bool = False
    # M13-45: the path's first part is a folder named `_..._` that discovery
    # did not take as a flavor (`Install.not_taken`), with why. Left out of
    # the JSON when None, so every other path prints as before.
    not_taken: install.NotTaken | None = Field(default=None, exclude_if=lambda v: v is None)
    # M16-18: for a relative path with nothing on disk and no row, what its
    # last part names: the files by that name in the flavor folders read,
    # then the rows that name it and match none of them. Left out of the
    # JSON when empty, so every other path prints as before.
    candidates: list[ExplainCandidate] = Field(default_factory=list, exclude_if=lambda v: not v)


# The option each view parameter is spelled with; `file` is `sv dump`'s
# positional argument. A test parses every view's argv with the Click tree.
_VIEW_OPTIONS = {
    "scope": "--scope",
    "account": "--account",
    "character": "--character",
    "root": "--root",
    "flavor": "--flavor",
}
_NO_READER = "no wowlab command shows this file's contents"


class _At(NamedTuple):
    """Where a classified path is, for the view table."""

    parts: tuple[str, ...]  # relative to its base, spelled as on disk
    absolute: str  # the path itself, inside the install
    flavor: str | None  # the flavor folder, for a flavor path
    folder: bool  # the path is a folder
    newest_log: str | None  # for a combat log: the name of the newest one, if any


_ViewSpec = tuple[tuple[str, ...], dict[str, str], str]


def _account(at: _At) -> dict[str, str]:
    """`--account` for a path under `WTF/Account/<ACCOUNT>/`."""
    return {"account": at.parts[2]}


def _character(at: _At) -> dict[str, str]:
    """`--account` and `--character <realm folder>/<character folder>` for a
    path under `WTF/Account/<ACCOUNT>/<Realm>/<Character>/`."""
    return {"account": at.parts[2], "character": f"{at.parts[3]}/{at.parts[4]}"}


def _flavor(at: _At) -> dict[str, str]:
    return {"flavor": at.flavor} if at.flavor is not None else {}


def _install_show(at: _At) -> list[_ViewSpec] | str:
    return [(("install", "show"), {}, "show the install's flavor folders, products and versions")]


def _sv_dump(at: _At) -> list[_ViewSpec] | str:
    return [(("sv", "dump"), {"file": at.absolute}, "print this file's values as data")]


def _addons_list(at: _At) -> list[_ViewSpec] | str:
    """For an addon folder, or a TOC file in one: `addons list` reads every
    addon's TOC files. Any other file in an addon is read by no command."""
    if at.folder or at.parts[-1].casefold().endswith(".toc"):
        return [(("addons", "list"), _flavor(at), "list installed addons and their TOC files")]
    return f"{_NO_READER}; `wowlab addons list` lists addon folders and their TOC files"


def _addons_check_view(at: _At) -> list[_ViewSpec] | str:
    """M16-34: a character's AddOns.txt is read by `addons check`, which
    names the characters that list each installed addon."""
    return [
        (
            ("addons", "check"),
            _flavor(at),
            "check each addon against this client, and which characters' AddOns.txt list it",
        )
    ]


def _addon_executable(at: _At) -> list[_ViewSpec] | str:
    """An addon folder named like a bundle (`Interface/AddOns/Foo.app/`) is
    still an addon folder, and `addons list` lists it. Anything inside an
    addon that is named like an executable, or lies inside such a folder, is
    read by no command."""
    if at.folder and len(at.parts) == 3:
        return _addons_list(at)
    return _NO_READER


def _combat_log(at: _At) -> list[_ViewSpec] | str:
    """`log tail` reads the newest combat log only."""
    if at.newest_log is not None and at.parts[-1] == at.newest_log:
        return [(("log", "tail"), _flavor(at), "print the last lines of this combat log")]
    newest = f" ({at.newest_log})" if at.newest_log is not None else ""
    return f"`wowlab log tail` reads only the newest combat log{newest}, not this one"


def _character_wtf(at: _At) -> list[_ViewSpec] | str:
    name = at.parts[-1].casefold()
    who = _flavor(at) | _character(at)
    if name == "config-cache.wtf":
        return [(("cvar", "list"), {"scope": "character"} | who, "list this character's CVars")]
    if name == "bindings-cache.wtf":
        return [(("binds", "list"), who, "list this character's key bindings")]
    if name == "macros-cache.txt":
        return [(("macros", "list"), who, "list this character's macros")]
    return _NO_READER


def _config_wtf(at: _At) -> list[_ViewSpec] | str:
    return [(("cvar", "list"), _flavor(at), "list the CVars in Config.wtf")]


def _account_config(at: _At) -> list[_ViewSpec] | str:
    params = {"scope": "account"} | _flavor(at) | _account(at)
    return [(("cvar", "list"), params, "list this account's CVars")]


def _account_binds(at: _At) -> list[_ViewSpec] | str:
    return [(("binds", "list"), _flavor(at) | _account(at), "list this account's key bindings")]


def _account_macros(at: _At) -> list[_ViewSpec] | str:
    return [(("macros", "list"), _flavor(at) | _account(at), "list this account's macros")]


# File-map entry id -> the existing read commands that decode such a file
# (§15.6), or why none does for this one. No decoder is written for a view:
# an entry no command reads has none.
_VIEWS: dict[str, Callable[[_At], list[_ViewSpec] | str]] = {
    "build-info": _install_show,
    "flavor-folder": _install_show,
    "flavor-info": _install_show,
    "config-wtf": _config_wtf,
    "account-blizzard-savedvariables": _sv_dump,
    "account-savedvariables": _sv_dump,
    "character-savedvariables": _sv_dump,
    "savedvariables-backup": _sv_dump,
    "client-savedvariables": _sv_dump,
    "client-savedvariables-backup": _sv_dump,
    "account-config-cache": _account_config,
    "account-bindings-cache": _account_binds,
    "account-macros-cache": _account_macros,
    "character-wtf-config": _character_wtf,
    "character-addons-txt": _addons_check_view,
    "addons-folder": _addons_list,
    "addon": _addons_list,
    "addon-executable": _addon_executable,
    "blizzard-addon-export": _addons_list,
    "combat-log": _combat_log,
}


def _view(command: tuple[str, ...], params: dict[str, str], does: str) -> ExplainView:
    argv = list(command)
    if "file" in params:
        argv.append(params["file"])
    for name, option in _VIEW_OPTIONS.items():
        if name in params:
            argv += [option, params[name]]
    return ExplainView(command=list(command), params=params, argv=argv, does=does)


def _views(entry_id: str, at: _At, root: str | None) -> tuple[list[ExplainView], str | None]:
    """The views of a path classified as `entry_id`, or why it has none
    (None for a folder with none). `root`: the install root, when `explain`
    was given `--root`, so each view reads the same install."""
    build = _VIEWS.get(entry_id)
    built = build(at) if build is not None else _NO_READER
    if isinstance(built, str):
        return [], None if at.folder else built
    with_root = {"root": root} if root is not None else {}
    return [_view(command, params | with_root, does) for command, params, does in built], None


def _hint(argv: Sequence[str], does: str) -> ExplainHint:
    shell = _shell()
    line = ["wowlab", *argv]
    command = _powershell_join(line) if shell == "powershell" else shlex.join(line)
    return ExplainHint(
        command=command,
        argv=list(argv),
        shell=shell,
        does=does,
        shell_label=_SHELL_LABELS[shell],
    )


def _hints(
    verdict: Verdict,
    facts: pathfacts.SnapshotFacts | None,
    flavor: str | None,
    root: str | None,
    views: Sequence[ExplainView],
    *,
    absent: bool = False,
    snapshot_hints: bool = True,
) -> list[ExplainHint]:
    """Copyable commands for one path. A restore hint only for a file the
    write gate would write (`restorable` is True: never a folder or a link)
    that the latest snapshot holds, when the file on disk differs from that
    copy or is gone. A first-snapshot hint for such a file when no snapshot
    covers it or none ever held it, and no manifest was unreadable. Neither
    without `snapshot_hints` (wowlab's own leftover temp file, M16-13)."""
    hints: list[ExplainHint] = []
    with_root = ["--root", root] if root is not None else []
    if snapshot_hints and verdict.restorable is True and facts is not None and facts.error is None:
        latest = facts.snapshot_id
        if (
            latest is not None
            and facts.holds
            and facts.path is not None
            and facts.live in ("differs", "missing")
            and facts.confirmed is True
            and not facts.damaged_newer
        ):
            what = (
                f"put back the copy in snapshot {latest}, which was {_KIND_WORDS['pre-write']}"
                if facts.kind == "pre-write"
                else f"put back the copy in snapshot {latest}"
            )
            does = f"{what}; it shows the plan and asks first, and `wowlab undo` reverses it"
            if verdict.server_may_replace:
                does += (
                    "; the server may replace it at the next login (sync settings: wowlab doctor)"
                )
            if verdict.level == "no":
                does += "; the client writes this file and replaces it at its next save"
            hints.append(
                _hint(["snap", "restore", latest, "--paths", facts.path, *with_root], does)
            )
        elif (
            facts.captured
            and facts.damaged == 0
            and flavor is not None
            and (latest is None or (not facts.holds and facts.last_held is None))
        ):
            hints.append(
                _hint(
                    ["snap", "create", "--flavor", flavor, *with_root],
                    _FIRST_SNAPSHOT_ABSENT if absent else _FIRST_SNAPSHOT,
                )
            )
    hints.extend(_hint(v.argv, v.does) for v in views)
    return hints


_FIRST_SNAPSHOT = "take a snapshot, so a later change to this file can be put back"
# M13-29: a named path a snapshot covers but does not hold is deleted by
# `snap restore --paths`.
_FIRST_SNAPSHOT_ABSENT = (
    "take a snapshot, so a file created here later can be removed with "
    "`snap restore --paths` naming it"
)
_ABSENT_VIEWS = "nothing is at this path, so no wowlab command can show it"


def _snap_name(ref: pathfacts.SnapshotRef) -> str:
    label = f', "{ref.label}"' if ref.label else ""
    return f"{ref.id}, {ref.created_at}{label}"


_UNCONFIRMED = (
    "neither name is on disk, and nothing on this drive shows whether it treats the two "
    "names as one"
)


def _snapshot_line(facts: pathfacts.SnapshotFacts, captured: str) -> str:
    """The "In the latest snapshot" line. `captured`: the subtrees `snap
    create` captures, in words."""
    # A newer automatic copy was passed over: the snapshot named is the
    # player's latest, not the latest of all (M16-09).
    whose = "your latest" if facts.newer_pre_write is not None else "the latest"
    head = f"In {whose} readable snapshot" if facts.damaged else f"In {whose} snapshot"
    if facts.error is not None:
        return f"{head}: unknown; the snapshot store could not be read ({facts.error})"
    damaged = ""
    if facts.damaged:
        damaged = (
            f"; {facts.damaged} snapshot manifest(s) could not be read (`wowlab snap list` "
            "names them), so this may be incomplete"
        )
    latest = (
        pathfacts.SnapshotRef(
            id=facts.snapshot_id, created_at=facts.created_at or "", label=facts.label or ""
        )
        if facts.snapshot_id is not None
        else None
    )
    if latest is None:
        if not facts.captured:
            return f"{head}: not held; snapshots capture {captured} only{damaged}"
        if facts.damaged:
            return f"{head}: no readable snapshot of this flavor folder covers it{damaged}"
        return f"{head}: none; no snapshot of this flavor folder has been taken in this install"
    if not facts.holds:
        if facts.last_held is not None:
            unconfirmed = (
                f", as {facts.last_held_path}, which could not be confirmed to be this file "
                f"({_UNCONFIRMED})"
                if facts.last_held_confirmed is False
                else ""
            )
            return (
                f"{head} ({_snap_name(latest)}): not held, the file was not there when it was "
                f"taken; the latest snapshot that holds it is {_snap_name(facts.last_held)}"
                f"{unconfirmed}{damaged}"
            )
        if facts.damaged:
            return (
                f"{head} ({_snap_name(latest)}): not held; no readable snapshot holds it{damaged}"
            )
        return (
            f"{head} ({_snap_name(latest)}): not held; no snapshot of this flavor folder in "
            "this install holds it"
        )
    on_disk = {
        "same": "the file on disk is the same",
        "differs": "the file on disk differs",
        "missing": "the file on disk is missing",
        "unreadable": "the file on disk could not be read",
    }[facts.live or "unreadable"]
    if facts.confirmed is False:
        return (
            f"{head} ({_snap_name(latest)}): it holds {facts.path}, which could not be "
            f"confirmed to be this file ({_UNCONFIRMED}); {on_disk}{_pre_write_words(facts)}"
            f"{damaged}"
        )
    return f"{head} ({_snap_name(latest)}): {on_disk}{_pre_write_words(facts)}{damaged}"


def _pre_write_words(facts: pathfacts.SnapshotFacts) -> str:
    """What the line says of a pre-write copy (M16-09): of the snapshot it
    names, when that is one, or of a newer one it passed over."""
    if facts.kind == "pre-write":
        return (
            "; this snapshot was "
            + _KIND_WORDS["pre-write"]
            + ", and no other snapshot covers this file"
        )
    newer = facts.newer_pre_write
    if newer is not None:
        return (
            f"; a newer snapshot ({_snap_name(newer)}) was taken automatically just "
            "before a change, and restoring that one puts the files back as they were "
            "just before that change"
        )
    return ""


def _path_kind(
    install_root: Path | str, result: layout.Classified | layout.Unclassified, query: str
) -> tuple[bool, bool, bool]:
    """`(folder, link, placeholder)` for `explain`'s path. `placeholder`
    (M13-32): some component on the way is a reparse point that is no link
    (a cloud placeholder, a deduplicated file) but that the write gate
    refuses all the same (`verdict.gate_refuses_stat`). `link`: some component from
    the install root down is a symlink, a junction or another Windows
    reparse point that names another path (`gaps.is_link`, snapshot's
    name-surrogate rule; a cloud placeholder or deduplicated file is not a
    link), so the path is a link or lies inside one; the walk stops there and
    never follows it. `folder`: a real folder, or one written with a trailing
    separator."""
    slash = query.endswith(("/", os.sep))
    if result.base is None:
        return slash, False, False
    under = [result.flavor_folder] if result.base == "flavor" and result.flavor_folder else []
    current = Path(install_root)
    st: os.stat_result | None = None
    placeholder = False
    for part in [*under, *[p for p in result.path.split("/") if p]]:
        current = current / part
        try:
            st = longpath.lstat(current)
        except OSError:
            return slash, False, placeholder  # absent from here down: nothing to follow
        if gaps.is_link(current, st):
            return False, True, False
        placeholder = placeholder or gate_refuses_stat(st)
    if st is None:  # the install root itself; how it is reached is the owner's choice
        return True, False, False
    return slash or stat.S_ISDIR(st.st_mode), False, placeholder


def _nowhere(
    inst: install.Install,
    given: Path,
    judged: layout.Classified | layout.Unclassified,
    *,
    relative: bool,
    chosen: str | None,
) -> bool:
    """Whether nothing is on disk at `explain`'s path under every reading of
    it (M13-29). A path typed relative to the install is read at the install
    root and in the chosen flavor folder (`--flavor`, or the only one), or,
    with several flavors and none chosen, in every one of them, so a file in
    any of those is there; an absolute path, or one relative to the current
    folder, is read once. `layout.absent` never looks behind a link, so a
    path with a link on the way is never "not found"."""
    if judged.base is None:
        return False
    if relative:
        base = Path(inst.root)
        folders = [chosen] if chosen is not None else [f.folder for f in inst.flavors]
        readings = [base / given, *(base / folder / given for folder in folders)]
    else:
        under = [judged.flavor_folder] if judged.base == "flavor" and judged.flavor_folder else []
        readings = [Path(inst.root).joinpath(*under, *[p for p in judged.path.split("/") if p])]
    return all(layout.absent(r, inst.root) for r in readings)


# Rows the client writes at logout or `/reload` (their Written-by cell);
# a test pins this set to the file map.
WRITTEN_AT_LOGOUT = frozenset(
    {"account-blizzard-savedvariables", "account-savedvariables", "character-savedvariables"}
)


def _absent_line(result: layout.Classified, folder: bool) -> str:
    """The "on disk" words for a path a row describes that is not there: a
    folder when written as one or when the row describes only folders; "a
    file or folder" when the path is exactly the fixed part of a `**` row
    (`Fonts`, `Interface/AddOns/<x>`); else a file."""
    e = result.entry
    parts = tuple(p for p in result.path.split("/") if p)
    if folder or not e.matches(parts, "file"):
        noun = "folder"
    elif e.heads_a_subtree(parts):
        noun = "file or folder"
    else:
        noun = "file"
    line = f"nothing at this path; this is what a {noun} here would be"
    if e.id in WRITTEN_AT_LOGOUT:
        line += "; the client writes it at logout or /reload"
    return line


_NOT_FOUND_AT_ROOT = (
    f"{layout.NOT_FOUND}, and no file-map row matches it at the install root; "
    "pass --flavor to see what it would be inside a flavor folder"
)


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _not_taken_at(
    inst: install.Install, found: layout.Classified | layout.Unclassified
) -> install.NotTaken | None:
    """The `_..._` folder not taken as a flavor that an install-root path
    starts with, if any (M13-45)."""
    if found.base != "root" or not found.path:
        return None
    return _not_taken_named(inst, found.path.split("/", 1)[0])


def _found_at_root(inst: install.Install, found: layout.Classified | layout.Unclassified) -> bool:
    """Whether an install-root path is there (M13-57): walking down from the
    root one `lstat` at a time, as `layout.absent` does, every part is found,
    or a link is (a path through a link at the root is there, as its
    absolute spelling says; the link is never followed to find out). Any
    error on the way is "not there", and the path is read as on main."""
    if found.base != "root" or not found.path:
        return False
    current = Path(inst.root)
    for part in (p for p in found.path.split("/") if p):
        current = current / part
        try:
            st = longpath.lstat(current)
        except (OSError, ValueError):
            return False
        if layout.is_link(current, st):
            return True
    return True


def _plain_arg(text: str) -> str:
    """A path argument as typed, except that on Windows a `\\\\?\\` spelling
    with a plain one is taken by that (`longpath.plain_text`), so no output,
    report or message echoes the prefix (M13-44)."""
    return longpath.plain_text(text) if longpath.ON_WINDOWS else text


def _stored_target(target: str | None) -> str | None:
    """A link target as a manifest stores it (`snapshot.Entry.target`, what
    `readlink` answered), for display: on Windows without the extended-length
    prefix an absolute target is answered with, where it has a plain spelling
    (`longpath.plain_text`, as `tree` shows a link, M13-43). The manifest
    keeps the raw text, which `guard` and `profiles` compare raw to raw; a
    relative target, and any target off Windows, is shown as stored (M13-52)."""
    if target is None or not longpath.ON_WINDOWS:
        return target
    return longpath.plain_text(target)


@app.command(short_help="What a file is, who writes it, whether wowlab may edit it.")
@_handled
def explain(
    path: Annotated[
        str,
        typer.Argument(
            help="Absolute; or relative to the current folder when it exists there; "
            "else relative to the install folder; else to the flavor folder."
        ),
    ],
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Also print the row's tier (how ordinary using this file is), "
            "what in wowlab reads it, and the paths the row covers.",
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """What a path in the install is, who writes it, whether wowlab may edit it.

    \f
    JSON: ExplainReport."""
    inst, _ = _discover(root)
    flavor = _given_flavor(flavor)  # $WOWLAB_FLAVOR reads as --flavor here too (M16-114)
    path = _plain_arg(path)
    given = Path(path)
    result: layout.Classified | layout.Unclassified
    judged: layout.Classified | layout.Unclassified | None = None
    here = Path.cwd() / given
    relative = False
    at_root = False  # a relative path judged at the install root only
    # The flavor folder a relative path is read in (M13-29): `--flavor`, or
    # the only one; None with several and none chosen.
    chosen_folder = inst.flavors[0].folder if len(inst.flavors) == 1 else None
    if given.is_absolute():
        result = layout.classify(path, inst)
    elif longpath.lexists(here) and _is_inside(here.absolute(), Path(inst.root).absolute()):
        result = layout.classify(str(here) + ("/" if path.endswith("/") else ""), inst)
    else:
        relative = True
        result = layout.classify(path, inst)
        # M13-45: a path under a `_..._` folder discovery did not take stays
        # at the install root, where that folder is; it is not re-read in a
        # flavor folder.
        taken_root = _not_taken_at(inst, result) is not None
        # M13-57: so does a path with no row either way (at the root or in
        # the flavor) that is there at the install root: a link, a file or
        # a folder, or a path through a link there. A row in the flavor wins.
        on_root = (
            not taken_root
            and isinstance(result, layout.Unclassified)
            and _found_at_root(inst, result)
        )
        at_root = taken_root
        if taken_root and flavor is not None and _not_taken_named(inst, flavor) is None:
            # The path is not read in it, but a `--flavor` that names no
            # folder at all is refused as on main.
            _select_flavor(inst, flavor)
        if (
            isinstance(result, layout.Unclassified)
            and (flavor or len(inst.flavors) == 1)
            and not taken_root
        ):
            chosen = _select_flavor(inst, flavor)
            chosen_folder = chosen.folder
            in_flavor = layout.Layout.for_flavor(chosen, inst.root).classify(path)
            if isinstance(in_flavor, layout.Classified) or layout.below_no_account(in_flavor):
                # M13-50: below a folder of WTF/Account/ that is no account,
                # the reason read in the flavor says why no row applies.
                result = in_flavor
            elif on_root:
                at_root = True  # M13-57: judged at the root, where it is
            elif result.base == "root" and result.path and in_flavor.base == "flavor":
                # No row either way: the verdict reads the path inside the
                # chosen flavor, so every spelling of a flavor path gets the
                # same one. `result` stays the install-root lookup, as on main.
                judged = in_flavor
        elif on_root:
            # Several flavors, none chosen: `--flavor` would not read it in
            # one unless that flavor has a row for it (M13-57).
            at_root = not any(
                isinstance(lay.classify(path), layout.Classified) for lay in layout.layouts(inst)
            )
    if judged is None:
        judged = result
    folder, link, placeholder = _path_kind(inst.root, judged, path)
    # M13-29: "not found" only when nothing is there under any reading of
    # the path; else the words are main's, whatever one reading found.
    nowhere = not link and _nowhere(inst, given, judged, relative=relative, chosen=chosen_folder)
    # Several flavors, none chosen: the path was looked up at the install
    # root only, and is in none of the flavor folders.
    unchosen = relative and chosen_folder is None and len(inst.flavors) > 1
    if isinstance(result, layout.Unclassified) and result.base is not None:
        if not nowhere and result.reason.startswith(layout.NOT_FOUND):
            result = result.model_copy(update={"reason": layout.NO_ROW})
        elif nowhere and unchosen and result.base == "root":
            result = result.model_copy(update={"reason": _NOT_FOUND_AT_ROOT})
        elif (
            nowhere
            and isinstance(judged, layout.Unclassified)
            and judged.reason.startswith(layout.NOT_FOUND)
        ):
            result = result.model_copy(update={"reason": judged.reason})
    # M13-23: the verdict judges the file as it is spelled on disk (a volume
    # that folds case or normalization opens it under any spelling); `query`
    # and `result` stay as typed. A path typed as stored is judged as before.
    parts = _disk_parts(inst, judged) if judged.base is not None and not link else None
    verdict = verdict_of(
        judged.entry if isinstance(judged, layout.Classified) else None,
        judged.base,
        "/".join(parts) if parts is not None else judged.path,
        folder=folder,
        link=link,
        placeholder=placeholder,
        missing=nowhere,
    )
    lay = _flavor_layout(inst, judged)
    stat_, facts, views, note, hints = _detail(
        inst, judged, parts or (), verdict, root, lay, folder=folder, link=link, absent=nowhere
    )
    captured = _captured_words(lay)
    if facts is not None:
        # M13-27: the line the text output prints, carried in the JSON so
        # `wowlab ui` shows Python's words and words nothing itself.
        facts = facts.model_copy(update={"words": _snapshot_line(facts, captured)})
    # M16-18: a relative path not on disk, with no row: what its last part
    # names, in the flavor folder it is read in (every one, with several and
    # none chosen).
    candidates: list[ExplainCandidate] = []
    if (
        relative
        and nowhere
        and isinstance(result, layout.Unclassified)
        and result.base is not None
        and _not_taken_at(inst, judged) is None
    ):
        folders = [chosen_folder] if chosen_folder else [f.folder for f in inst.flavors]
        candidates = _explain_candidates(inst, path, folders)
    report = ExplainReport(
        query=path,
        result=result,
        verdict=verdict,
        stat=stat_,
        snapshot=facts,
        views=views,
        views_note=note,
        hints=hints,
        plain=_plain_text(result.entry) if isinstance(result, layout.Classified) else None,
        absent=nowhere,
        not_taken=_not_taken_at(inst, judged),
        candidates=candidates,
    )
    # M16-18: a path that matches nothing (outside the install, or nothing
    # on disk under any reading, no row, and nothing by its name) exits 1.
    matched = not (
        isinstance(result, layout.Unclassified)
        and (result.base is None or nowhere)
        and not candidates
    )
    if json_out:
        _emit(report)
        if not matched:
            raise typer.Exit(EXIT_ERROR)
        return
    taken = report.not_taken
    if isinstance(result, layout.Unclassified):
        where = f" ({result.flavor_folder})" if result.flavor_folder else ""
        if nowhere and result.reason.startswith(layout.NOT_FOUND):
            # M13-29: what is (not) there first, then what the map says.
            _say(f"{path}{where}: {layout.NOT_FOUND}")
            _say(f"  {result.reason.removeprefix(layout.NOT_FOUND + ', and ')}")
        elif verdict.link and taken is not None:
            # M13-45: a link not taken as a flavor folder, or a path inside
            # one, says so first, as the verdict `--json` carries does. Any
            # other link prints as before.
            _say(f"{path}{where}: {verdict.words}")
        else:
            _say(f"{path}: no file-map row{where}: {result.reason}")
        if taken is not None:
            _say(f"  {taken.name}: {taken.note}")
        if (
            len(inst.flavors) > 1
            and flavor is None
            and not given.is_absolute()
            and not nowhere
            and not at_root  # M13-57: `--flavor` would not read it in one
        ):
            _say("  (with several flavors, pass --flavor to read PATH inside one of them)")
        _say_detail(report, captured)
        if candidates:
            _say_candidates(inst, candidates, root)
        if not matched:
            raise typer.Exit(EXIT_ERROR)
        return
    e = result.entry
    base = f"in flavor folder {result.flavor_folder}" if result.base == "flavor" else "install root"
    _say(f"{result.path or '.'} ({base})")
    _say(f"  what:         {_player_copy(_plain(e.what))}")
    _say(f"  written by:   {_player_copy(_plain(e.written_by))}")
    _say(f"  edit:         {report.verdict.words}")
    # M16-17: the tier, the reader and the row's own path pattern are the
    # spec's words; they print only on request (`--json` always has them).
    if verbose:
        _say(f"  tier:         {tier_words(e.tier)}")
        _say(f"  read by:      {_reader_words(e.module)}")
        _say(f"  file-map row: {e.id} ({e.doc_path})")
    else:
        _say(f"  file-map row: {e.id}")
    if taken is not None:  # no install-root row matches below `_..._` today
        _say(f"  {taken.name}: {taken.note}")
    if nowhere:
        _say(f"  on disk:      {_absent_line(result, folder)}")
    _say_detail(report, captured)


def _explain_candidates(
    inst: install.Install, path: str, folders: Sequence[str]
) -> list[ExplainCandidate]:
    """M16-18: the paths in `folders` (flavor folders) whose last parts are
    `path`'s, with their rows; then the rows that name it and that none of
    those paths has. Empty for a path with `.` or `..` in it."""
    parts = list(PurePath(path).parts)
    if not parts or any(p in (".", "..") for p in parts):
        return []
    found: list[ExplainCandidate] = []
    for folder in folders:
        base = Path(inst.root) / folder
        for rel in layout.named_paths(base, parts):
            got = layout.classify(base / rel, inst)
            entry = got.entry if isinstance(got, layout.Classified) else None
            found.append(
                ExplainCandidate(
                    path=rel,
                    flavor_folder=folder,
                    entry_id=entry.id if entry else None,
                    doc_path=_plain(entry.doc_path) if entry else None,
                )
            )
    seen = {c.entry_id for c in found}
    rows = [
        ExplainCandidate(path=None, flavor_folder=None, entry_id=e.id, doc_path=_plain(e.doc_path))
        for e in filemap.load().named(parts)
        if e.id not in seen
    ]
    return found + rows


def _say_candidates(
    inst: install.Install, candidates: Sequence[ExplainCandidate], root: Path | None
) -> None:
    """M16-18: what a name not found as typed may mean, listed, and each
    explained when there are `_CANDIDATES_EXPLAINED` or fewer."""
    paths = [c for c in candidates if c.path is not None]
    rows = [c for c in candidates if c.path is None]
    if paths:
        _say(f"  on disk by this name ({len(paths)}):")
        for c in paths[:_CANDIDATES_SHOWN]:
            # Each spelled so that `explain` takes it as typed: from the
            # flavor folder with one flavor, from the install root with
            # several (a flavor-relative path then needs --flavor).
            if len(inst.flavors) == 1:
                _say(f"    {c.path} (in {c.flavor_folder})")
            else:
                _say(f"    {c.flavor_folder}/{c.path}")
        if len(paths) > _CANDIDATES_SHOWN:
            _say(f"    … and {len(paths) - _CANDIDATES_SHOWN} more (--json lists them all)")
    if rows:
        _say(f"  file-map rows for a file by this name ({len(rows)}):")
        for c in rows[:_CANDIDATES_SHOWN]:
            _say(f"    {c.entry_id}: {c.doc_path}")
        if len(rows) > _CANDIDATES_SHOWN:
            _say(f"    … and {len(rows) - _CANDIDATES_SHOWN} more (--json lists them all)")
    if len(candidates) > _CANDIDATES_EXPLAINED:
        _say("  explain one of them by the path shown")
        return
    fm = filemap.load()
    for c in candidates:
        _say("")
        if c.path is not None:
            # Named from the install root, so it is read where it was found.
            explain(path=f"{c.flavor_folder}/{c.path}", root=root, flavor=None, json_out=False)
        elif c.entry_id is not None:
            e = fm.entry(c.entry_id)
            _say(f"{c.doc_path} (file-map row {e.id}; no file for this row was found)")
            _say(f"  what:         {_player_copy(_plain(e.what))}")
            _say(f"  written by:   {_plain(e.written_by)}")


def _say_detail(report: ExplainReport, captured: str) -> None:
    """The lines M13-23 adds after the ones `explain` printed before it."""
    st = report.stat
    if st is not None:
        what = _bytes(st.size) if st.size is not None else f"a {st.kind}"
        _say(f"  on disk:      {what}, modified {st.mtime}")
    if report.snapshot is not None:
        _say(f"  {report.snapshot.words or _snapshot_line(report.snapshot, captured)}")
    if report.views_note is not None:
        _say(f"  views:        {report.views_note}")
    for h in report.hints:
        _say(f"  hint: {h.command}  # {h.does}")


def _captured_words(lay: layout.Layout | None) -> str:
    """The subtrees `snap create` captures, in words, from `layout`."""
    if lay is None:
        return "the flavor's default subtrees"
    plain = [f"{s}/" for s in lay.snapshot_subtrees()]
    extra = [f"{s}/" for s in lay.snapshot_subtrees(screenshots=True) if f"{s}/" not in plain]
    words = ", ".join(plain)
    if extra:
        words += f" ({', '.join(extra)} with --screenshots)"
    return words


def _flavor_layout(
    inst: install.Install, judged: layout.Classified | layout.Unclassified
) -> layout.Layout | None:
    if judged.base != "flavor" or not judged.flavor_folder:
        return None
    match = [f for f in inst.flavors if f.folder == judged.flavor_folder]
    return layout.Layout.for_flavor(match[0], inst.root) if match else None


def _disk_parts(
    inst: install.Install, judged: layout.Classified | layout.Unclassified
) -> tuple[str, ...]:
    """`judged.path`'s parts, spelled as on disk (`pathfacts.disk_spelling`)."""
    under = [judged.flavor_folder] if judged.base == "flavor" and judged.flavor_folder else []
    base = Path(inst.root).joinpath(*under)
    return pathfacts.disk_spelling(base, [p for p in judged.path.split("/") if p])


def _detail(
    inst: install.Install,
    judged: layout.Classified | layout.Unclassified,
    parts: tuple[str, ...],
    verdict: Verdict,
    root: Path | None,
    lay: layout.Layout | None,
    *,
    folder: bool,
    link: bool,
    absent: bool = False,
) -> tuple[
    pathfacts.PathStat | None,
    pathfacts.SnapshotFacts | None,
    list[ExplainView],
    str | None,
    list[ExplainHint],
]:
    """`explain`'s stat, snapshot facts, views and hints for `judged`. Nothing
    for a path outside the install or reached through a link: no `lstat`
    through it, no hash, no decoder pointed at it, no restore hint. The path
    is respelled as it is on disk first, so a volume that folds case gives
    every spelling the same answer. `absent` (M13-29): nothing is at the
    path, so no view (each would fail on it) and the first-snapshot hint
    says what a snapshot does for a file created there later."""
    if judged.base is None or link:
        return None, None, [], None, []
    under = [judged.flavor_folder] if judged.base == "flavor" and judged.flavor_folder else []
    target = Path(inst.root).joinpath(*under, *parts)
    given_root = str(inst.root) if root is not None and str(root) != "" else None
    stat_ = pathfacts.path_stat(target)
    facts: pathfacts.SnapshotFacts | None = None
    if lay is not None and judged.flavor_folder and parts and not folder:
        facts = pathfacts.snapshot_facts(
            snapshot.SnapshotStore(),
            Path(inst.root),
            judged.flavor_folder,
            "/".join(parts),
            lay.snapshot_subtrees(),
            _pre_write_ids(snapshot.SnapshotStore()),
        )
    views: list[ExplainView] = []
    note: str | None = None
    if isinstance(judged, layout.Classified) and absent:
        note = _ABSENT_VIEWS
    elif isinstance(judged, layout.Classified):
        newest: str | None = None
        if judged.entry.id == "combat-log" and lay is not None:
            logs = _logs_dir(lay)
            found = combatlog.newest_log(logs) if logs is not None else None
            newest = found.name if found is not None else None
        at = _At(
            parts=parts,
            absolute=str(target),
            flavor=judged.flavor_folder,
            folder=folder,
            newest_log=newest,
        )
        views, note = _views(judged.entry.id, at, given_root)
    leftover = isinstance(judged, layout.Classified) and judged.entry.id == WOWLAB_TEMP_ROW
    hints = _hints(
        verdict,
        facts,
        judged.flavor_folder,
        given_root,
        views,
        absent=absent,
        snapshot_hints=not leftover,
    )
    return stat_, facts, views, note, hints
