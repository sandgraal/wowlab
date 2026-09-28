"""`wowlab looks page` (M11-07, docs/LAB_PLAN.md §13.2, ADR-0027).

The page is graded on its data and its CSP, not on browser behaviour
(ADR-0027): the JSON block parsed back must be what `looks races`, `looks
options` and `looks show` print for the same selections, and what
`looks.Customizations` says; the Content-Security-Policy must allow nothing
but the page's own inline style and script, and the file must name no
external resource. The tables are the 1.60.1.70009 recordings, served
through `test_cli_looks`'s fixtures (no network, ADR-0012; no install found,
user data redirected into `tmp_path`).

Inputs labelled `constructed` (L8): a damaged look file, installs made of a
bare `.build.info` or `.flavor.info`, a link into one, hostile strings for
the JSON block.
"""

# ruff: noqa: F811  (fixtures imported from test_cli_looks are requested by name)

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

import pytest
from test_cli_looks import (
    BUILD,
    DRUID,
    EXPORTED_ONLY,
    FACE_ALLOWED,
    HUMAN,
    NIGHT_ELF,
    PLAIN_SKIN,
    WARRIOR,
    _json,
    _save,
    _Source,
    _tree,
    model,  # noqa: F401
    no_install,  # noqa: F401  (autouse: discovery finds no install)
    ok,
    run,
    source,  # noqa: F401  (autouse: recorded tables only)
    user_data,  # noqa: F401  (autouse: user data in tmp_path)
)

from wowlab_core import cli, lookspage, lookstore
from wowlab_core.looks import Customizations

# ─── helpers ─────────────────────────────────────────────────────────────────


class _Page(HTMLParser):
    """Every tag with its attributes, and the text of each script and style."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self.styles: list[str] = []
        self._open: tuple[str, dict[str, str | None]] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag in ("script", "style"):
            self._open = (tag, dict(attrs))
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._open is not None and tag == self._open[0]:
            text = "".join(self._text)
            if tag == "script":
                self.scripts.append((self._open[1], text))
            else:
                self.styles.append(text)
            self._open = None


def _parsed(page: str) -> _Page:
    parser = _Page()
    parser.feed(page)
    parser.close()
    return parser


def _hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


def _page(*extra: str) -> tuple[cli.LooksPageReport, str]:
    report = _json(cli.LooksPageReport, "looks", "page", "--build", BUILD, *extra)
    return report, Path(report.path).read_text(encoding="utf-8")


def _data(page: str) -> cli.LooksPageData:
    return cli.LooksPageData.model_validate_json(lookspage.embedded_json(page))


def _view(data: cli.LooksPageData, race: int, body: int, class_id: int | None) -> list[int]:
    [view] = [
        v for v in data.views if (v.race_id, v.body_type, v.class_id) == (race, body, class_id)
    ]
    return view.options


# ─── the embedded JSON is the model's data ───────────────────────────────────


def test_page_is_written_under_the_user_data_directory(user_data: Path) -> None:
    report, page = _page()
    assert Path(report.path) == (user_data / "pages" / "looks.html").resolve()
    assert report.build == BUILD
    assert report.bytes == len(page.encode("utf-8"))
    assert report.damaged == []
    assert page.startswith("<!DOCTYPE html>")


def test_embedded_json_parses_back_to_the_payload() -> None:
    _, page = _page()
    text = lookspage.embedded_json(page)
    data = cli.LooksPageData.model_validate_json(text)
    assert json.loads(text) == data.model_dump(mode="json")
    assert data.format == 1 and data.build == BUILD


def test_races_and_classes_are_the_tables(model: Customizations) -> None:
    _, page = _page()
    data = _data(page)
    printed = _json(cli.LooksRacesReport, "looks", "races", "--build", BUILD)
    assert [r.race for r in data.races] == printed.races
    assert [r.race.id for r in data.races] == [r.id for r in model.playable_races()]
    assert data.races_heading == f"Races flagged playable in build {BUILD}'s ChrRaces"
    # Class ids come from ChrClasses, nothing else (L6).
    assert [(c.id, c.name) for c in data.classes] == [
        (i, c.name) for i, c in sorted(model.classes.items())
    ]
    assert "Warrior (1)" in data.notes[2] and f"build {BUILD}'s ChrClasses" in data.notes[2]
    # Race and class are not checked as a pair, and the page says so.
    assert data.notes[2].endswith(
        "Every class is offered with every race: which races can be which class is not read "
        "from the tables [verify], so a pairing here (a Human Druid, say) may be one the game "
        "does not offer."
    )


def test_every_view_is_options_for(model: Customizations) -> None:
    """One view per playable race, body type and class (or none), each listing
    exactly the options `options_for` gives, in its order."""
    _, page = _page()
    data = _data(page)
    expected = [
        (race.id, body.body_type, class_id)
        for race in model.playable_races()
        for body in race.body_types
        for class_id in [None, *sorted(model.classes)]
    ]
    assert [(v.race_id, v.body_type, v.class_id) for v in data.views] == expected
    for view in data.views:
        assert [data.options[i].id for i in view.options] == [
            o.id for o in model.options_for(view.race_id, view.body_type, view.class_id)
        ]
        assert view.chr_model_id == model.races[view.race_id].model_for(view.body_type)
        if view.class_id is None:
            assert view.notes == [cli._PAGE_NO_CLASS_NOTE]
        else:
            assert view.notes == [
                "Race and class are not checked as a pair: this view lists what the "
                "customization tables give this race and class, not proof that the game lets "
                "anyone create it."
            ]
    assert len(data.options) == len({o.model_dump_json() for o in data.options})  # deduplicated


@pytest.mark.parametrize(
    ("race", "body", "class_id"),
    [(HUMAN, 0, None), (HUMAN, 0, WARRIOR), (HUMAN, 1, DRUID), (NIGHT_ELF, 1, DRUID)],
)
def test_views_hold_what_looks_options_prints(race: int, body: int, class_id: int | None) -> None:
    """Every finding on a choice or option is the CLI's own, refusals and
    notes split as `looks options` prints them."""
    _, page = _page()
    data = _data(page)
    args = ["looks", "options", str(race), "--sex", str(body), "--build", BUILD]
    if class_id is not None:
        args += ["--class", str(class_id)]
    [printed] = _json(cli.LooksOptionsReport, *args).body_types
    shown = [data.options[i] for i in _view(data, race, body, class_id)]
    assert shown == [cli._page_option(o) for o in printed.options]
    for page_option, option in zip(shown, printed.options, strict=True):
        assert page_option.refusals + page_option.notes == sorted(
            option.notes, key=lambda f: not f.refuses
        )
        assert all(f.refuses for f in page_option.refusals)
        assert not any(f.refuses for f in page_option.notes)
        assert page_option.choices == option.choices


def test_the_class_view_carries_the_class_refusal() -> None:
    """A warrior's view refuses the death-knight skin; the no-class view notes it."""
    _, page = _page()
    data = _data(page)

    def dk_skin(class_id: int | None) -> cli.LooksChoice:
        [skin] = [
            data.options[i] for i in _view(data, HUMAN, 0, class_id) if data.options[i].id == 9
        ]
        return next(c for c in skin.choices if c.id == 13)

    assert dk_skin(WARRIOR).refusals and dk_skin(WARRIOR).refusals[0].kind == "class_excluded"
    assert dk_skin(None).refusals == []
    assert "class_restricted" in {f.kind for f in dk_skin(None).notes}


def test_page_carries_the_clis_caveats() -> None:
    _, page = _page()
    data = _data(page)
    assert data.notes[0] == cli._PLAYABLE_NOTE
    assert "not a claim about what a server lets anyone create" in data.notes[0]
    assert data.notes[1] == EXPORTED_ONLY  # the tables-only remark
    assert any('"needs <unlock>"' in line for line in data.legend)
    assert any(line.startswith("refused: ") for line in data.legend)
    assert any(line.startswith("note: shown, never a refusal") for line in data.legend)
    assert data.no_class_label == "not given (class-restricted choices are noted, not refused)"
    assert data.legend[0] == (
        "Each choice is checked as a look that holds only that choice, as `wowlab looks "
        "options` does, so a dependency on another option shows as a note, never a refusal."
    )
    assert data.legend[2].endswith(
        ", a class-restricted choice when no class is chosen, conditions, and options on a "
        "model no race uses (a form, pet or mount), checked by their requirements only "
        "[verify]."
    )
    for label in (
        '<label>Class (not checked against the race) <select id="class"></select></label>',
        "<label>Body type (numbered as the tables number it [verify]) "
        '<select id="body"></select></label>',
        '<div class="sub">In <span id="looks-directory"></span>, as they were when this page '
        "was written; run <code>wowlab looks page</code> again to include looks saved "
        "since.</div>",
        '"form, pet or mount option [verify]"',
        'plural(refusals, "refusal")',
    ):
        assert label in page, label


def test_saved_looks_are_what_looks_show_prints() -> None:
    assert _save("fine", f"9={PLAIN_SKIN}", f"10={FACE_ALLOWED}").exit_code == 0
    assert _save("hotfix", "9=999999").exit_code == 0  # a note, not a refusal
    _, page = _page()
    data = _data(page)
    assert [entry.report.name for entry in data.looks] == ["fine", "hotfix"]
    for entry in data.looks:
        printed = _json(cli.LookReport, "looks", "show", entry.report.name, "--build", BUILD)
        # The page writes the home directory as ~ (the tests' user data is not
        # under it, so the path is unchanged here; see the ~ test).
        assert entry.report == cli._page_report(printed)
        assert entry.choice_lines == [cli._ref_text(ref) for ref in printed.choices]
        assert entry.verdict == f"not refused, {len(printed.notes)} note(s)"
    hotfix = data.looks[1].report
    assert [f.kind for f in hotfix.notes] == ["unknown_to_build"]
    assert "(possibly a hotfix)" in hotfix.notes[0].message
    assert EXPORTED_ONLY in hotfix.remarks


def test_page_with_a_damaged_look_is_written_and_exits_1_constructed(user_data: Path) -> None:
    assert _save("good", f"9={PLAIN_SKIN}").exit_code == 0
    (user_data / "looks" / "broken.json").write_text("{not json", encoding="utf-8")
    result = run("looks", "page", "--build", BUILD, "--json")
    assert result.exit_code == 1
    report = cli.LooksPageReport.model_validate_json(result.stdout)
    assert [d.file for d in report.damaged] == ["broken.json"]
    assert "damaged look file broken.json" in result.stderr
    data = _data(Path(report.path).read_text(encoding="utf-8"))
    assert [e.report.name for e in data.looks] == ["good"]
    assert [d.file for d in data.damaged] == ["broken.json"]


def test_page_is_deterministic(tmp_path: Path) -> None:
    ok("looks", "page", "--build", BUILD, "--out", str(tmp_path / "a.html"))
    ok("looks", "page", "--build", BUILD, "--out", str(tmp_path / "b.html"))
    assert (tmp_path / "a.html").read_bytes() == (tmp_path / "b.html").read_bytes()


def test_page_text_output_and_replace(tmp_path: Path) -> None:
    out = tmp_path / "some" / "folder" / "mine.html"
    text = ok("looks", "page", "--build", BUILD, "--out", str(out)).stdout
    assert f"Wrote the looks page for build {BUILD}: {out.resolve()}" in text
    assert "makes no network requests" in text
    first = out.read_bytes()
    ok("looks", "page", "--build", BUILD, "--out", str(out))  # replaces its own page
    assert out.read_bytes() == first
    assert sorted(p.name for p in out.parent.iterdir()) == ["mine.html"]  # no temp left


def test_page_without_an_install_or_build_says_pass_build() -> None:
    result = run("looks", "page")
    assert result.exit_code == 1 and "pass --build" in result.stderr


# ─── CSP and no external resource ────────────────────────────────────────────

_EXTERNAL_ATTRS = {
    "src",
    "href",
    "srcset",
    "action",
    "formaction",
    "ping",
    "poster",
    "data",
    "background",
    "manifest",
    "xlink:href",
}
_EXTERNAL_TAGS = {
    "link",
    "img",
    "iframe",
    "frame",
    "object",
    "embed",
    "form",
    "base",
    "audio",
    "video",
    "source",
    "track",
    "picture",
    "svg",
    "portal",
    "applet",
}


def _csp(parsed: _Page) -> dict[str, list[str]]:
    metas = [
        attrs
        for tag, attrs in parsed.tags
        if tag == "meta" and (attrs.get("http-equiv") or "").lower() == "content-security-policy"
    ]
    assert len(metas) == 1
    directives: dict[str, list[str]] = {}
    for part in (metas[0]["content"] or "").split(";"):
        name, *values = part.split()
        directives[name] = values
    return directives


def test_csp_allows_only_the_pages_own_style_and_script() -> None:
    _, page = _page()
    parsed = _parsed(page)
    csp = _csp(parsed)
    assert csp["default-src"] == ["'none'"]
    for fetch in ("img-src", "font-src", "connect-src", "media-src", "object-src", "frame-src"):
        assert csp[fetch] == ["'none'"], fetch
    assert csp["base-uri"] == ["'none'"] and csp["form-action"] == ["'none'"]
    executable = [text for attrs, text in parsed.scripts if attrs.get("type") is None]
    data_blocks = [attrs for attrs, _ in parsed.scripts if attrs.get("type") is not None]
    assert len(executable) == 1
    assert data_blocks == [{"type": "application/json", "id": lookspage.DATA_ELEMENT_ID}]
    assert csp["script-src"] == [f"'{_hash(executable[0])}'"]
    assert len(parsed.styles) == 1
    assert csp["style-src"] == [f"'{_hash(parsed.styles[0])}'"]
    assert lookspage.content_security_policy() == "; ".join(
        f"{name} {' '.join(values)}" for name, values in csp.items()
    )
    # The meta tag comes before anything it governs.
    assert page.index("Content-Security-Policy") < page.index("<style>")


def test_page_names_no_external_resource() -> None:
    assert _save("fine", f"9={PLAIN_SKIN}").exit_code == 0
    _, page = _page()
    parsed = _parsed(page)
    assert "://" not in page
    assert not re.search(r"(?i)\bunsafe-(inline|eval)\b|\bdata:|\bblob:", page)
    for tag, attrs in parsed.tags:
        assert tag not in _EXTERNAL_TAGS, tag
        assert not _EXTERNAL_ATTRS & set(attrs), (tag, attrs)
        assert not any(name.startswith("on") for name in attrs), (tag, attrs)  # no inline handlers
    [style] = parsed.styles
    assert "url(" not in style and "@import" not in style and "@font-face" not in style
    [script] = [text for attrs, text in parsed.scripts if attrs.get("type") is None]
    for call in (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "import(",
        "importScripts",
        "Worker(",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "Function(",
        "window.open",
        "location",
        ".src",
        ".href",
    ):
        assert call not in script, call


def test_the_template_names_no_build_or_flavor() -> None:
    """L6: the page's own text is static; only the data block names a build."""
    page = lookspage.render("{}")
    for word in (BUILD, "70009", "_classic_", "_retail_", "wow_classic"):
        assert word not in page


@pytest.mark.parametrize(
    "hostile",
    [
        "</script><script>alert(1)</script>",
        "<!-- <script>",
        "</SCRIPT >",
        "a & b > c",
        "line" + chr(0x2028) + "separator" + chr(0x2029) + "paragraph",
    ],
)
def test_the_json_block_cannot_be_closed_by_its_data_constructed(hostile: str) -> None:
    document = json.dumps({"name": hostile, "list": [hostile]}, ensure_ascii=False)
    page = lookspage.render(document)
    text = lookspage.embedded_json(page)
    assert json.loads(text) == {"name": hostile, "list": [hostile]}
    assert "<" not in text and ">" not in text and "&" not in text
    parsed = _parsed(page)
    assert len(parsed.scripts) == 2  # the data block and the viewer, nothing injected


# ─── never into an install ───────────────────────────────────────────────────


def _install(tmp_path: Path, marker: str = ".build.info") -> Path:
    """Constructed: an install is a folder holding the marker."""
    root = tmp_path / "game"
    root.mkdir()
    (root / marker).write_bytes(b"")
    return root


@pytest.mark.parametrize("marker", [".build.info", ".flavor.info"])
@pytest.mark.parametrize("where", ["looks.html", "sub/dir/looks.html", ""])
def test_out_inside_an_install_is_refused_constructed(
    tmp_path: Path, source: _Source, user_data: Path, marker: str, where: str
) -> None:
    root = _install(tmp_path, marker)
    before = _tree(root)
    out = root / where if where else root
    result = run("looks", "page", "--build", BUILD, "--out", str(out))
    assert result.exit_code == 1
    assert "refused:" in result.stderr
    assert "inside a game install" in result.stderr
    assert f"holds {marker}" in result.stderr
    assert "generated pages never live in an install (L1)" in result.stderr
    assert "nothing was written" in result.stderr
    assert _tree(root) == before
    assert source.asked == []  # refused before any work
    assert not (user_data / "pages").exists()


def test_out_through_a_link_into_an_install_is_refused_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    (root / "Interface").mkdir()
    before = _tree(root)
    link = tmp_path / "elsewhere"
    link.symlink_to(root / "Interface", target_is_directory=True)
    result = run("looks", "page", "--build", BUILD, "--out", str(link / "looks.html"))
    assert result.exit_code == 1 and "inside a game install" in result.stderr
    assert _tree(root) == before


def test_write_page_refuses_an_install_itself_constructed(tmp_path: Path) -> None:
    """The library check, apart from the command's early one."""
    root = _install(tmp_path)
    with pytest.raises(lookstore.LookLocationError, match="inside a game install"):
        lookspage.write_page("<!DOCTYPE html>", root / "x" / "looks.html")
    assert sorted(p.name for p in root.iterdir()) == [".build.info"]


def test_out_that_is_a_directory_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "pages"
    folder.mkdir()
    result = run("looks", "page", "--build", BUILD, "--out", str(folder))
    assert result.exit_code == 1 and "is a directory" in result.stderr
    assert list(folder.iterdir()) == []


def test_the_default_page_folder_inside_an_install_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user data directory inside an install: the default path is refused too."""
    root = _install(tmp_path)
    monkeypatch.setattr(
        lookspage.platformdirs, "user_data_path", lambda *a, **k: root / "data" / "wowlab"
    )
    result = run("looks", "page", "--build", BUILD)
    assert result.exit_code == 1 and "inside a game install" in result.stderr
    assert sorted(p.name for p in root.iterdir()) == [".build.info"]


# ─── round 1 (#104 security review): the user data directory, other files ────


@pytest.mark.parametrize(
    "where",
    ["page.html", "looks/page.html", "looks/mine.json", "store/objects/page.html", "gamedata/x"],
)
def test_out_in_the_user_data_directory_outside_pages_is_refused(
    user_data: Path, source: _Source, where: str
) -> None:
    before = _tree(user_data) if user_data.exists() else {}
    result = run("looks", "page", "--build", BUILD, "--out", str(user_data / where))
    assert result.exit_code == 1
    assert "inside wowlab's user data directory" in result.stderr
    assert "not in its pages folder" in result.stderr
    assert "nothing was written" in result.stderr
    assert (_tree(user_data) if user_data.exists() else {}) == before
    assert source.asked == []


def test_out_in_a_subfolder_of_pages_is_allowed(user_data: Path) -> None:
    out = user_data / "pages" / "old" / "looks.html"
    ok("looks", "page", "--build", BUILD, "--out", str(out))
    assert out.read_bytes().startswith(lookspage.PAGE_HEADER)


def test_every_page_starts_with_the_header() -> None:
    assert lookspage.render("{}").encode("utf-8").startswith(lookspage.PAGE_HEADER)
    _, page = _page()
    assert page.encode("utf-8").startswith(lookspage.PAGE_HEADER)


@pytest.mark.parametrize(
    "body",
    [
        b"my notes, not a page",
        b"",
        b"<!DOCTYPE html>\n<html><head><title>someone else's page</title>",
        lookspage.PAGE_HEADER[:-1],
    ],
)
def test_an_existing_file_that_is_not_a_page_is_not_replaced_constructed(
    tmp_path: Path, source: _Source, body: bytes
) -> None:
    folder = tmp_path / "out"
    folder.mkdir()
    out = folder / "notes.html"
    out.write_bytes(body)
    result = run("looks", "page", "--build", BUILD, "--out", str(out))
    assert result.exit_code == 1
    assert "exists and is not a page wowlab wrote" in result.stderr
    assert "nothing was written" in result.stderr
    assert out.read_bytes() == body
    assert sorted(p.name for p in folder.iterdir()) == ["notes.html"]
    assert source.asked == []


def test_an_existing_page_is_replaced_constructed(tmp_path: Path) -> None:
    """Constructed: a file that begins with the header (an older page) is ours."""
    out = tmp_path / "looks.html"
    out.write_bytes(lookspage.PAGE_HEADER + b"an older page")
    ok("looks", "page", "--build", BUILD, "--out", str(out))
    assert _data(out.read_text(encoding="utf-8")).build == BUILD


def _loop(tmp_path: Path) -> Path:
    """Constructed: two links that point at each other, in `<tmp>/loop/`."""
    folder = tmp_path / "loop"
    folder.mkdir()
    (folder / "a").symlink_to(folder / "b")
    (folder / "b").symlink_to(folder / "a")
    return folder / "a"


def test_a_symlink_loop_is_a_clean_refusal_constructed(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    result = run("looks", "page", "--build", BUILD, "--out", str(loop / "looks.html"))
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # not a traceback
    assert "cannot be resolved" in result.stderr and "nothing was written" in result.stderr
    assert sorted(p.name for p in loop.parent.iterdir()) == ["a", "b"]


def test_refuse_install_refuses_a_symlink_loop_constructed(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    with pytest.raises(lookstore.LookLocationError, match="cannot be resolved"):
        lookstore.refuse_install(loop / "x")
    with pytest.raises(lookstore.LookLocationError, match="cannot be resolved"):
        lookstore.LookStore(loop / "looks").save(
            lookstore.SavedLook(
                saved_build=BUILD,
                look=cli.looks.Look(name="mine", race_id=HUMAN, body_type=0),
            )
        )


def test_the_page_shows_home_as_a_tilde(
    tmp_path: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page passed on does not carry the account's user name in its paths."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert _save("fine", f"9={PLAIN_SKIN}").exit_code == 0
    (user_data / "looks" / "broken.json").write_text("{not json", encoding="utf-8")
    result = run("looks", "page", "--build", BUILD, "--json")
    assert result.exit_code == 1  # the damaged file
    report = cli.LooksPageReport.model_validate_json(result.stdout)
    page = Path(report.path).read_text(encoding="utf-8")
    data = _data(page)
    looks_dir = "~/" + (user_data / "looks").relative_to(tmp_path).as_posix()
    assert data.looks_directory == looks_dir
    assert data.looks[0].report.path == f"{looks_dir}/fine.json"
    assert dict(data.looks[0].facts)["file"] == f"{looks_dir}/fine.json"
    assert data.damaged[0].error.startswith(f"{looks_dir}/broken.json ")
    assert str(tmp_path) not in page
    printed = _json(cli.LookReport, "looks", "show", "fine", "--build", BUILD)
    assert printed.path == str(user_data / "looks" / "fine.json")  # the CLI keeps it whole
    assert data.looks[0].report == printed.model_copy(update={"path": f"{looks_dir}/fine.json"})


# ─── round 2 (#104 security review): other spellings of the user data path ───

_FIRMLINK = Path("/System/Volumes/Data")


def _refused_inside_user_data(out: Path, source: _Source) -> None:
    result = run("looks", "page", "--build", BUILD, "--out", str(out))
    assert result.exit_code == 1, result.stdout
    assert "inside wowlab's user data directory" in result.stderr
    assert "nothing was written" in result.stderr
    assert source.asked == []
    assert not out.exists()


def _store_dirs(user_data: Path) -> None:
    for sub in ("store/objects", "gamedata/tables", "looks"):
        (user_data / sub).mkdir(parents=True, exist_ok=True)


@pytest.mark.parametrize("sub", ["store/objects", "gamedata/tables", "looks"])
def test_a_case_variant_of_the_user_data_path_is_refused_constructed(
    user_data: Path, source: _Source, sub: str
) -> None:
    """Constructed: `WOWLAB` for `wowlab`, on a volume that ignores case."""
    _store_dirs(user_data)
    variant = user_data.with_name(user_data.name.upper())
    if not variant.exists():
        pytest.skip("this volume is case-sensitive")
    _refused_inside_user_data(variant / sub / "page.html", source)
    assert "page.html" not in {p.name for p in user_data.rglob("*")}


def test_a_case_variant_is_refused_before_the_folder_exists_constructed(
    user_data: Path, source: _Source
) -> None:
    """By the folded text, on any volume: nothing to compare identity with yet."""
    assert not user_data.exists()
    variant = user_data.with_name(user_data.name.upper())
    _refused_inside_user_data(variant / "store" / "page.html", source)


def test_a_case_variant_of_pages_is_still_pages_constructed(user_data: Path) -> None:
    (user_data / "pages").mkdir(parents=True)
    out = user_data.with_name(user_data.name.upper()) / "PAGES" / "looks.html"
    ok("looks", "page", "--build", BUILD, "--out", str(out))
    assert (user_data / "pages" / "looks.html").read_bytes().startswith(lookspage.PAGE_HEADER)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS firmlinks")
def test_a_firmlink_spelling_of_the_user_data_path_is_refused_constructed(
    user_data: Path, source: _Source
) -> None:
    """Constructed: `/System/Volumes/Data/private/var/...` for `/private/var/...`."""
    _store_dirs(user_data)
    real = user_data.resolve()
    variant = Path(str(_FIRMLINK) + str(real))
    if not variant.exists() or not os.path.samestat(variant.stat(), real.stat()):
        pytest.skip("the user data folder has no firmlink spelling here")
    _refused_inside_user_data(variant / "gamedata" / "tables" / "page.html", source)


def _accented(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Constructed: a user data directory whose name has an accent (NFC)."""
    data = tmp_path / unicodedata.normalize("NFC", "donn\u00e9es") / "wowlab"
    monkeypatch.setattr(lookspage.platformdirs, "user_data_path", lambda *a, **k: data)
    return data


def _nfd(path: Path) -> Path:
    return Path(unicodedata.normalize("NFD", str(path)))


def test_an_nfd_spelling_of_the_user_data_path_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: _Source
) -> None:
    data = _accented(tmp_path, monkeypatch)
    _store_dirs(data)
    variant = _nfd(data)
    assert str(variant) != str(data)
    if not variant.exists():
        pytest.skip("this volume keeps NFC and NFD names apart")
    _refused_inside_user_data(variant / "store" / "objects" / "page.html", source)


def test_an_nfd_spelling_is_refused_before_the_folder_exists_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: _Source
) -> None:
    data = _accented(tmp_path, monkeypatch)
    _refused_inside_user_data(_nfd(data) / "looks" / "page.html", source)
    assert not data.parent.exists()


def test_the_user_data_folder_itself_and_a_dotdot_route_are_inside_constructed(
    user_data: Path, source: _Source
) -> None:
    _store_dirs(user_data)
    _refused_inside_user_data(user_data / "looks" / ".." / "store" / "page.html", source)
    _refused_inside_user_data(user_data / "page.html", source)
