"""`linkstore`: the addresses a player saved for their characters (M21-03).

Every case runs in `tmp_path`, with the user data directory redirected
there: the file is written whole beside itself, never inside an install,
and every saved address is checked again when it is read, so a file
edited by hand never puts another address on the page. The edited and
damaged files are `constructed` (L8: there is no external format here;
the file is wowlab's own).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from _user_dirs import redirect_user_dirs

from wowlab_core import links, linkstore

FT = "https://forevertwinking.com/armory/13-sandgraal-rizzen"
BUILD = "https://forevertwinking.com/builds/dwhe4ngw2p"
WCL = "https://forever.warcraftlogs.com/reports/aBcD1234eFgH5678"
KEY = linkstore.CharacterKey(
    flavor="_forever_", account="LABACCOUNTA", character="1/Sandgraal-Rizzen"
)
OTHER = linkstore.CharacterKey(
    flavor="_forever_", account="LABACCOUNTA", character="1/Tay-Soulrend"
)
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    path = linkstore.default_path()
    assert tmp_path in path.parents
    return path


def _saved(*urls: str, key: linkstore.CharacterKey = KEY) -> linkstore.StoreFile:
    file = linkstore.StoreFile()
    for url in urls:
        file, _, _ = linkstore.save(file, key, links.check_pasted(url), now=NOW)
    return file


def test_no_file_is_no_links_and_creates_nothing(data: Path) -> None:
    stored = linkstore.read()
    assert stored.file.characters == [] and stored.refused == []
    assert not data.parent.exists()


def test_a_saved_file_reads_back_the_same(data: Path) -> None:
    file = _saved(FT, BUILD, WCL)
    linkstore.write(file)
    assert linkstore.read().file == file
    assert [s.url for s in linkstore.links_for(file, KEY)] == [FT, BUILD, WCL]
    assert linkstore.links_for(file, OTHER) == []
    assert [p.name for p in data.parent.iterdir()] == ["links.json"]  # no temporary left
    assert json.loads(data.read_text(encoding="utf-8"))["format"] == 1


def test_a_new_address_of_the_same_kind_replaces_the_old() -> None:
    file = _saved(FT)
    newer = "https://forevertwinking.com/armory/14-sandgraal-rizzen"
    after, new, old = linkstore.save(file, KEY, links.check_pasted(newer), now=NOW)
    assert old is not None and old.url == FT and new.url == newer
    assert [s.url for s in linkstore.links_for(after, KEY)] == [newer]
    assert new.saved_at == "2026-11-23T12:00:00Z"


def test_forget_drops_one_kind_or_the_whole_site() -> None:
    file = _saved(FT, BUILD, WCL)
    after, dropped = linkstore.forget(file, KEY, "forevertwinking", "build")
    assert [s.url for s in dropped] == [BUILD]
    assert [s.url for s in linkstore.links_for(after, KEY)] == [FT, WCL]
    after, dropped = linkstore.forget(file, KEY, "forevertwinking")
    assert [s.url for s in dropped] == [FT, BUILD]
    after, dropped = linkstore.forget(after, KEY, "warcraftlogs")
    assert after.characters == [] and [s.url for s in dropped] == [WCL]


def test_each_character_keeps_its_own() -> None:
    file = _saved(FT)
    file, _, _ = linkstore.save(file, OTHER, links.check_pasted(BUILD), now=NOW)
    assert [s.url for s in linkstore.links_for(file, KEY)] == [FT]
    assert [s.url for s in linkstore.links_for(file, OTHER)] == [BUILD]


@pytest.mark.parametrize(
    ("site", "kind", "url"),
    [
        ("forevertwinking", "profile", "https://evil.example/armory/13-x"),
        ("forevertwinking", "profile", "javascript:alert(1)"),
        ("forevertwinking", "profile", "https://u:p@forevertwinking.com/armory/13-x"),
        ("forevertwinking", "profile", BUILD),  # a build filed as a profile
        ("warcraftlogs", "report", FT),  # another site's address
        ("blizzard", "profile", "https://worldofwarcraft.blizzard.com/x"),
        ("warcraftlogs", "report", WCL + "#fight=last"),  # not as wowlab keeps it
    ],
    ids=lambda v: f"constructed: {v!r}",
)
def test_an_entry_edited_by_hand_is_refused_when_read_constructed(
    data: Path, site: str, kind: str, url: str
) -> None:
    good = _saved(FT, key=OTHER)
    linkstore.write(good)
    edited = json.loads(data.read_text(encoding="utf-8"))
    edited["characters"].append(
        {
            "key": KEY.model_dump(),
            "links": [{"site": site, "kind": kind, "url": url, "saved_at": "x"}],
        }
    )
    data.write_text(json.dumps(edited), encoding="utf-8")
    stored = linkstore.read()
    assert linkstore.links_for(stored.shown, KEY) == []
    assert [(r.key, r.site, r.kind) for r in stored.refused] == [(KEY, site, kind)]
    assert [s.url for s in linkstore.links_for(stored.shown, OTHER)] == [FT]
    # Not shown, but still in the file as read: a change for another
    # character writes it back untouched (code review, M21-03).
    assert [s.url for s in linkstore.links_for(stored.file, KEY)] == [url]
    after, _, _ = linkstore.save(stored.file, OTHER, links.check_pasted(BUILD), now=NOW)
    assert [s.url for s in linkstore.links_for(after, KEY)] == [url]


@pytest.mark.parametrize(
    "content",
    [b"not json", b"{}" + b" " * 10, b'{"format": 2, "characters": []}', b"[]", b'{"x": 1}'],
    ids=lambda v: f"constructed: {v[:20]!r}",
)
def test_a_damaged_file_is_refused_constructed(data: Path, content: bytes) -> None:
    data.parent.mkdir(parents=True)
    data.write_bytes(content)
    if content.startswith(b"{}"):
        # An empty object is a file with no characters (every field defaults).
        assert linkstore.read().file.characters == []
        return
    with pytest.raises(linkstore.LinkStoreError):
        linkstore.read()


def test_a_file_over_the_limit_is_refused_unread_constructed(data: Path) -> None:
    data.parent.mkdir(parents=True)
    data.write_bytes(b" " * (linkstore.MAX_STORE_BYTES + 1))
    with pytest.raises(linkstore.LinkStoreError, match="limit"):
        linkstore.read()


def test_a_folder_in_place_of_the_file_is_refused_constructed(data: Path) -> None:
    data.mkdir(parents=True)
    with pytest.raises(linkstore.LinkStoreError):
        linkstore.read()


def test_the_store_is_never_inside_an_install_constructed(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    (root / "_forever_").mkdir(parents=True)
    (root / ".build.info").write_bytes(b"constructed\n")
    inside = root / "_forever_" / "wowlab" / "links.json"
    with pytest.raises(linkstore.LinkStoreError, match="install"):
        linkstore.write(_saved(FT), inside)
    with pytest.raises(linkstore.LinkStoreError, match="install"):
        linkstore.read(inside)
    assert not (root / "_forever_" / "wowlab").exists()


def test_the_refusals_say_what_is_wrong(data: Path) -> None:
    file = linkstore.StoreFile(
        characters=[
            linkstore.CharacterLinks(
                key=KEY,
                links=[
                    linkstore.StoredLink(
                        site="forevertwinking", kind="profile", url=BUILD, saved_at="x"
                    ),
                    linkstore.StoredLink(
                        site="warcraftlogs", kind="report", url=WCL + "?x=1", saved_at="x"
                    ),
                ],
            )
        ]
    )
    shown, refused = linkstore.checked(file)
    assert shown.characters == []
    assert [r.why for r in refused] == [
        "the address is a ForeverTwinking build address, not a profile address",
        f"the address is not kept as wowlab keeps it (that would be {WCL})",
    ]
