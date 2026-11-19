"""`scripts/charmodel_facts.py` on a constructed install (M15-11; labelled
`constructed`): its refusals, which are `renderplan_facts.py`'s (an output
already there, one inside an install, a missing table), and a plan whose
files the install does not hold ends with exit 1, nothing written and
nothing in the install changed; and the held-items probe's walk of the bone
array (M15-24) on a stand-in body model. The real record is written on the
owner's install (`fixtures/macos-70959/derived/charmodel.json`)."""

from __future__ import annotations

import importlib.util
import random
import struct
import sys
from pathlib import Path
from types import ModuleType

import pytest
from _casc_build import FLAVOR, make_source_install, tree_state
from test_renderplan_facts_script import SANGRAAL, _tables

from wowlab_core import m2

pytestmark = pytest.mark.parser

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "charmodel_facts.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("charmodel_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load()


def _install(tmp: Path) -> Path:
    return make_source_install(
        tmp / "install", stored=[], encoding_rows=[], root_blocks=[], rng=random.Random(10)
    )


def _run(root: Path, tables: Path, out: Path) -> int:
    return int(
        script.main(
            [
                "--root",
                str(root),
                "--flavor",
                FLAVOR,
                "--tables",
                str(tables),
                "--char",
                f"Sangraal-Sett={SANGRAAL}",
                "--captured",
                "2026-11-18",
                "--out",
                str(out),
            ]
        )
    )


def test_files_the_install_lacks_end_with_exit_1_and_nothing_written_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install(tmp_path)
    before = tree_state(root)
    out = tmp_path / "out.json"
    assert _run(root, _tables(tmp_path), out) == 1
    assert capsys.readouterr().err.startswith("charmodel_facts: ")
    assert not out.exists()
    assert tree_state(root) == before


def test_refuses_an_output_already_there_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.json"
    out.write_text("{}", encoding="utf-8")
    assert _run(_install(tmp_path), _tables(tmp_path), out) == 1
    assert "never replaced" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "{}"


def test_refuses_an_output_inside_an_install_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install(tmp_path)
    before = tree_state(root)
    assert _run(root, _tables(tmp_path), root / "out.json") == 1
    assert "inside a game install" in capsys.readouterr().err
    assert tree_state(root) == before


def test_refuses_a_missing_table_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.json"
    assert _run(_install(tmp_path), _tables(tmp_path, skip="ChrModelTextureLayer"), out) == 1
    assert "no ChrModelTextureLayer for" in capsys.readouterr().err
    assert not out.exists()


class _Body:
    """A stand-in for `m2.M2Model` with only what the probe of M15-24 reads:
    an MD21 chunk holding a bone array, and the attachments (constructed)."""

    def __init__(
        self,
        bones: list[tuple[int, tuple[float, float, float]]],
        points: list[tuple[int, int, tuple[float, float, float]]],
    ) -> None:
        self.attachments = tuple(
            m2.M2Attachment(index=i, id=pid, bone=bone, position=pos)
            for i, (pid, bone, pos) in enumerate(points)
        )
        at = 0x100
        blob = bytearray(at + 88 * len(bones))
        struct.pack_into("<II", blob, 0x2C, len(bones), at)
        for i, (parent, pivot) in enumerate(bones):
            struct.pack_into("<h", blob, at + 88 * i + 8, parent)
            struct.pack_into("<3f", blob, at + 88 * i + 76, *pivot)
        self.data = bytes(blob)
        self.chunks = (m2.M2Chunk(tag="MD21", offset=0, size=len(blob), read=True),)

    def attachment(self, point_id: int) -> m2.M2Attachment | None:
        return next((a for a in self.attachments if a.id == point_id), None)


ZERO = (0.0, 0.0, 0.0)


def test_held_point_parents_stop_at_a_root_or_after_two_levels_constructed() -> None:
    bones = [
        (-1, (0.0, 0.0, 1.0)),  # 0: a root away from the origin
        (0, ZERO),  # 1
        (1, ZERO),  # 2: point 1's bone; its parent 1 at the origin, then 0
        (-1, ZERO),  # 3: a root at the origin
        (3, ZERO),  # 4: point 0's bone; its parent 3 is a root
        (2, ZERO),  # 5
        (5, ZERO),  # 6: point 2's bone; 5 and 2 both at the origin: two levels only
    ]
    body = _Body(bones, [(0, 4, ZERO), (1, 2, ZERO), (2, 6, ZERO)])
    assert script.held_point_parents(body, body.data) == {
        "0": [{"bone": 3, "bone_pivot": [0.0, 0.0, 0.0]}],
        "1": [
            {"bone": 1, "bone_pivot": [0.0, 0.0, 0.0]},
            {"bone": 0, "bone_pivot": [0.0, 0.0, 1.0]},
        ],
        "2": [
            {"bone": 5, "bone_pivot": [0.0, 0.0, 0.0]},
            {"bone": 2, "bone_pivot": [0.0, 0.0, 0.0]},
        ],
    }


def test_held_point_parents_stop_at_a_parent_away_from_the_origin_constructed() -> None:
    body = _Body([(-1, ZERO), (0, (0.5, 0.0, 0.0)), (1, ZERO)], [(1, 2, ZERO)])
    assert script.held_point_parents(body, body.data) == {
        "1": [{"bone": 1, "bone_pivot": [0.5, 0.0, 0.0]}]
    }


def test_other_attachment_points_skip_the_recorded_seven_in_file_order_constructed() -> None:
    body = _Body(
        [(-1, ZERO), (0, (0.25, 0.5, 1.5))],
        [(27, 1, (0.25, 0.5, 1.5)), (1, 0, ZERO), (12, 1, ZERO), (26, 0, ZERO)],
    )
    assert script.other_attachment_points(body, body.data) == [
        {"id": 27, "bone": 1, "position": [0.25, 0.5, 1.5], "bone_pivot": [0.25, 0.5, 1.5]},
        {"id": 26, "bone": 0, "position": [0.0, 0.0, 0.0], "bone_pivot": [0.0, 0.0, 0.0]},
    ]


def test_a_bone_past_the_array_is_refused_constructed() -> None:
    body = _Body([(-1, ZERO)], [(1, 0, ZERO), (26, 3, ZERO)])
    with pytest.raises(script.facts.FactsError, match="bone 3 of 1"):
        script.other_attachment_points(body, body.data)
    parentless = _Body([(5, ZERO)], [(1, 0, ZERO)])
    with pytest.raises(script.facts.FactsError, match="bone 5 of 1"):
        script.held_point_parents(parentless, parentless.data)
