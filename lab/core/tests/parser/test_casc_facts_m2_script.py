"""`scripts/casc_facts.py --m2` on a constructed install (M15-08; labelled
`constructed`): it reads the models `--model` names and every skin their
`SFID` names through `casc` and `m2`, writes counts, ids and tags only,
says which files are not local, and changes nothing in the install."""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest
from _casc_build import FLAVOR, blte, ckey_of, key_in, make_source_install, tree_state, z_block
from _m2_build import chunked, md20, skin

pytestmark = pytest.mark.parser

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "casc_facts.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("casc_facts_m2", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


casc_facts = _load()


def _run(root: Path, out: Path, *extra: str) -> int:
    args = ["--root", str(root), "--flavor", FLAVOR, "--captured", "2026-11-17", "--out", str(out)]
    return int(casc_facts.main([*args, *extra]))


def _install(tmp_path: Path) -> Path:
    """A model (id 100) whose SFID names a view skin (500, local) and an LOD
    skin (501, listed in the root but not local), and a model (id 200) with
    no local copy."""
    rng = random.Random(1508)
    model = chunked(bytes(md20()), sfid=(500, 501), txid=(900,))
    view = skin(sections=((0, 0, 0, 3, 0, 3), (1301, 0, 0, 3, 0, 3)), batches=((0, 0, 0),))
    absent = b"an LOD skin never fetched"
    ek = {n: key_in(b, rng) + rng.randbytes(7) for n, b in (("m", 1), ("v", 2), ("a", 1))}
    stored = [(ek["m"], blte([z_block(model)])), (ek["v"], blte([z_block(view)]))]
    rows = [
        (ckey_of(model), len(model), [ek["m"]]),
        (ckey_of(view), len(view), [ek["v"]]),
        (ckey_of(absent), len(absent), [ek["a"]]),
    ]
    blocks = [
        (
            [100, 200, 500, 501],
            [ckey_of(model), ckey_of(absent), ckey_of(view), ckey_of(absent)],
            0,
            0x2,
        )
    ]
    return make_source_install(
        tmp_path / "install", stored=stored, encoding_rows=rows, root_blocks=blocks, rng=rng
    )


def test_writes_the_m2_record_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    before = tree_state(root)
    out = tmp_path / "out" / "m2-models.json"
    out.parent.mkdir()
    assert _run(root, out, "--m2", "--model", "built=100", "--model", "gone=200") == 0
    assert tree_state(root) == before
    facts = json.loads(out.read_text(encoding="utf-8"))
    assert facts["kind"] == "m2-models-derived" and facts["build"] == 65432
    built, gone = facts["models"]
    assert gone == {
        "name": "gone",
        "file_data_id": 200,
        "state": "not_local",
        "words": gone["words"],
    }
    assert (built["version"], built["md20_name"], built["vertex_count"]) == (272, "Constructed", 3)
    assert [c["tag"] for c in built["chunks"]] == ["MD21", "LDV1", "AFID", "SFID", "TXID"]
    assert built["textures"] == [{"type": 0, "flags": 3, "file_data_id": 900}]
    assert built["texture_file_data_ids"] == [900]
    assert (built["skin_file_data_ids"], built["lod_skin_file_data_ids"]) == ([500], [501])
    assert built["attachment_ids"] == [11] and built["bone_count"] == 2
    assert built["position_min"] == [0.0, 0.0, 0.0] and built["position_max"] == [1.0, 0.0, 2.0]
    view, lod = built["skins"]
    assert view["geoset_ids"] == [0, 1301] and view["levels"] == {"0": 2}
    assert view["lookup_is_identity"] is True and view["vertex_base"] == 0
    assert (view["index_end"], view["vertex_end"]) == (3, 3)
    assert lod == {"file_data_id": 501, "state": "not_local"}
    assert (view["skin_header_size"], view["shadow_batch_count"]) == (64, 0)
    assert view["header_bytes_0x38_to_0x40_zero"] is True
    # The triangle (0,0,0) (1,0,0) (0,0,2): edges 1, sqrt(5) and 2.
    edge = round((1 + 5**0.5 + 2) / 3, 5)
    assert view["mean_edge_with_vertex_base"] == view["mean_edge_from_vertex_0"] == edge
    assert built["head_attachment_z"] == 1.75
    text = out.read_text(encoding="utf-8")
    assert str(tmp_path) not in text and '_key"' not in text and "hash" not in text


def test_m2_needs_a_model_constructed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "m2.json"
    assert _run(tmp_path, out, "--m2") == 1
    assert "--m2 needs at least one --model" in capsys.readouterr().err
    assert not out.exists()


def test_a_model_argument_is_name_equals_id_constructed() -> None:
    assert casc_facts._m2_named("humanmale_hd=1011653") == ("humanmale_hd", 1011653)
    with pytest.raises(Exception, match="NAME=FILE_DATA_ID"):
        casc_facts._m2_named("humanmale_hd")


def test_a_malformed_model_is_one_line_and_writes_nothing_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rng = random.Random(15081)
    bad = b"MD21" + (2**32 - 1).to_bytes(4, "little")
    ek = key_in(1, rng) + rng.randbytes(7)
    root = make_source_install(
        tmp_path / "install",
        stored=[(ek, blte([z_block(bad)]))],
        encoding_rows=[(ckey_of(bad), len(bad), [ek])],
        root_blocks=[([100], [ckey_of(bad)], 0, 0x2)],
        rng=rng,
    )
    out = tmp_path / "m2.json"
    assert _run(root, out, "--m2", "--model", "bad=100") == 1
    err = capsys.readouterr().err
    assert err.startswith("casc_facts: chunk 'MD21' at offset 0") and err.count("\n") == 1
    assert not out.exists()


def test_model_without_m2_is_refused_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "m2.json"
    assert _run(tmp_path, out, "--model", "built=100") == 1
    assert "--model is read only with --m2" in capsys.readouterr().err
    assert not out.exists()
