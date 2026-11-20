"""Constructed game files for the `char model` tests (M15-11; ADR-0033 layer
a: no byte of a real install file). Every file a render plan names gets a
constructed stand-in: the body model an M2 whose textures are the body's
types (1 the body, 6 hair, 19 eyes, 2 the cape, 0 a file of its own), with
one triangle per section for a fixed list of geoset ids and the attachment
points the plans use; each item model a one-section M2 with one type-2
texture; each texture a 4 x 4 BGRA BLP, which fits every section of the
recorded layouts (all multiples of 4). The plans come from the real
`macos-70450` lab-addon records and the recorded wago tables
(`fixtures/wago/*.1.60.1.70959*`), read by `scripts/renderplan_facts.py`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import ModuleType

from wowlab_core import casc, labaddon, renderplan

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

import _m2_build as mb
from _blp_build import blp_file

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
TABLES_BUILD = "1.60.1.70959"
ACCOUNT = "90000001#6"
CHARS = FIXTURES / "macos-70450" / "forever" / "WTF" / "Account" / ACCOUNT / "1"
SCRIPT = HERE.parents[2] / "scripts" / "renderplan_facts.py"

# The body's geoset ids, one triangle each: 0 the body; 2 and 5, hairstyles
# of group 0 (Sangraal-Sett's record chooses 2); 101 and 103 of group 1
# (it chooses 103); 401 and 402, gloves; 501 and 502, the feet; 1505 and
# 1506, capes (its cloak selects 1506); 1701, a hidden group; 3201, a group
# shown by default.
BODY_GEOSETS = (0, 2, 5, 101, 103, 401, 402, 501, 502, 1505, 1506, 1701, 3201)
# The body's M2 textures by index: (type, TXID).
BODY_TEXTURES = ((1, 0), (6, 0), (19, 0), (2, 0), (0, 7001))
# Which texture each geoset's batch uses (the texture combo index is the
# texture index here); every other geoset uses the eyes' (2).
BODY_TEXTURE_OF = {0: 0, 2: 1, 5: 1, 1505: 3, 1506: 3, 3201: 4}
Point = tuple[int, int, tuple[float, float, float]]  # id, bone, position
ATTACHMENTS: tuple[Point, ...] = (
    (11, 0, (0.0, 0.0, 2.0)),
    (6, 0, (0.0, 0.5, 1.6)),
    (5, 0, (0.0, -0.5, 1.6)),
    (12, 0, (-0.2, 0.0, 1.4)),
    (1, 0, (0.1, 0.6, 1.0)),
    (2, 0, (0.1, -0.6, 1.0)),
    (0, 0, (0.1, -0.7, 1.1)),
)
TXID_FILE = 7001
SKIN_OFFSET = 50_000_000


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("renderplan_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tables() -> renderplan.RenderTables:
    return _script().read_tables(FIXTURES / "wago", TABLES_BUILD)  # type: ignore[no-any-return]


def record(label: str) -> labaddon.CharDB:
    return labaddon.read_char(CHARS / label / "SavedVariables" / "WowLab.lua")


def texture(rgba: tuple[int, int, int, int] = (200, 100, 50, 255)) -> bytes:
    r, g, b, a = rgba
    return blp_file(4, 4, 3, 8, 8, bytes((b, g, r, a)) * 16)


def _triangles(geosets: Iterable[int]) -> tuple[list[mb.Vertex], list[mb.Section]]:
    vertices: list[mb.Vertex] = []
    sections: list[mb.Section] = []
    for n, gid in enumerate(geosets):
        z = float(n)
        for p, uv in (
            ((0.0, 0.0, z), (0.0, 0.0)),
            ((1.0, 0.0, z), (1.0, 0.0)),
            ((0.0, 1.0, z), (0.0, 1.0)),
        ):
            vertices.append((p, b"\xff\0\0\0", b"\0\0\0\0", (0.0, 0.0, 1.0), uv, (0.0, 0.0)))
        sections.append((gid, 0, 3 * n, 3, 3 * n, 3))
    return vertices, sections


# The held-item points stored at the origin, as both fixture body models at
# 70959 store them (macos-70959/derived/charmodel.json).
ORIGIN_HANDS = tuple((a, b, (0.0, 0.0, 0.0) if a in (0, 1, 2) else p) for a, b, p in ATTACHMENTS)


def at_origin(points: tuple[int, ...]) -> tuple[Point, ...]:
    """`ATTACHMENTS` with `points` stored at the origin."""
    return tuple((a, b, (0.0, 0.0, 0.0) if a in points else p) for a, b, p in ATTACHMENTS)


def body_files(
    fdid: int,
    attachments: tuple[Point, ...] = ATTACHMENTS,
    *,
    bone_list: list[mb.Bone] | None = None,
    global_sequences: tuple[int, ...] | list[int] = (),
) -> dict[int, bytes]:
    vertices, sections = _triangles(BODY_GEOSETS)
    textures = tuple((kind, 0) for kind, _ in BODY_TEXTURES)
    blob = mb.md20(
        vertices=vertices,
        textures=textures,
        materials=((0, 0), (0x4, 1)),
        combos=tuple(range(len(BODY_TEXTURES))),
        attachments=attachments,
        lookup=(),
        bone_list=bone_list,
        global_sequences=global_sequences,
    )
    skin_id = fdid + SKIN_OFFSET
    model = mb.chunked(
        bytes(blob), sfid=(skin_id,), txid=tuple(t for _, t in BODY_TEXTURES), extra=()
    )
    n = len(sections)
    skin = mb.skin(
        lookup=tuple(range(3 * n)),
        indices=tuple(range(3 * n)),
        sections=sections,
        batches=tuple(
            (i, 1 if BODY_TEXTURE_OF.get(gid) == 1 else 0, BODY_TEXTURE_OF.get(gid, 2))
            for i, gid in enumerate(BODY_GEOSETS)
        ),
    )
    return {fdid: model, skin_id: skin, TXID_FILE: texture((10, 20, 30, 255))}


DERIVED = FIXTURES / "macos-70959" / "derived" / "charmodel.json"
SHEATH = tuple(range(26, 34))


def derived_character(body_fdid: int) -> dict[str, object]:
    """The derived fixture's row for the body model `body_fdid` (959310 or
    917116; read from the install by `scripts/charmodel_facts.py`)."""
    rows = json.loads(DERIVED.read_text(encoding="utf-8"))["characters"]
    return next(c for c in rows if c["body_file_data_id"] == body_fdid)  # type: ignore[no-any-return]


def fixture_sheath(body_fdid: int) -> tuple[tuple[Point, ...], list[mb.Bone], list[int]]:
    """The sheath points 26 to 33 of a fixture body model as the derived
    fixture records them (M15-24, M15-26): each point (id, bone, position),
    and the bones under them with their pivots, their parents with theirs,
    and each sheath bone's rotation track holding its recorded first key
    (encoded back to i16, so it decodes within 2e-5 of the recorded value);
    the global sequences the tracks name, with their recorded durations. A
    parent's parent the fixture does not record is a root at the origin: in
    the rest pose a parent turns nothing, whatever its pivot. Every other
    bone is a root at the origin with no track."""
    row: dict[str, object] = derived_character(body_fdid)
    points = [p for p in row["other_attachment_points"] if p["id"] in SHEATH]  # type: ignore[attr-defined]
    recorded = row["bones"]
    parents = row["sheath_point_parents"]
    keys = row["sheath_rotation_keys"]
    bones: dict[int, mb.Bone] = {}
    loops: dict[int, int] = {}
    for p in points:
        pid = str(p["id"])
        for parent in parents[pid]:  # type: ignore[index]
            up = recorded.get(str(parent["bone"]), {}).get("parent", -1)  # type: ignore[attr-defined]
            bones[parent["bone"]] = (
                up if str(up) in recorded else -1,
                tuple(parent["bone_pivot"]),
                None,
            )
        track = keys[pid]["rotation"]  # type: ignore[index]
        rotation = None
        if track["global_sequence"] >= 0 and track["first_array"] is not None:
            loops[track["global_sequence"]] = track["global_duration"]
            rotation = (
                track["global_sequence"],
                track["interpolation"],
                [mb.quat(*track["first_array"]["first_value"])],
            )
        parent_of = recorded[str(p["bone"])]["parent"]  # type: ignore[index]
        bones[p["bone"]] = (parent_of, tuple(p["bone_pivot"]), rotation)
    count = max(bones) + 1
    bone_list = [bones.get(i, (-1, (0.0, 0.0, 0.0), None)) for i in range(count)]
    durations = [loops.get(i, 1000) for i in range(max(loops, default=-1) + 1)]
    sheath = tuple((p["id"], p["bone"], tuple(p["position"])) for p in points)
    return sheath, bone_list, durations  # type: ignore[return-value]


def fixture_body_files(fdid: int) -> dict[int, bytes]:
    """`body_files` for a fixture body model (959310, 917116): the hand
    points at the origin as the real files store them, and the sheath points
    and their bones from the derived fixture (`fixture_sheath`)."""
    sheath, bone_list, durations = fixture_sheath(fdid)
    return body_files(fdid, ORIGIN_HANDS + sheath, bone_list=bone_list, global_sequences=durations)


def item_files(fdid: int, geoset: int = 0) -> dict[int, bytes]:
    vertices, sections = _triangles((geoset,))
    blob = mb.md20(vertices=vertices, textures=((2, 0),), combos=(0,), attachments=(), lookup=())
    skin_id = fdid + SKIN_OFFSET
    return {
        fdid: mb.chunked(bytes(blob), sfid=(skin_id,), txid=(0,), extra=()),
        skin_id: mb.skin(sections=sections),
    }


def many_textures_item(fdid: int, count: int, first_texture: int) -> dict[int, bytes]:
    """An item model of `count` sections, each drawn with a texture of its
    own (type 0, `TXID` `first_texture + i`), and those textures."""
    vertices, sections = _triangles([0] * count)
    blob = mb.md20(
        vertices=vertices,
        textures=((0, 0),) * count,
        combos=tuple(range(count)),
        attachments=(),
        lookup=(),
    )
    skin_id = fdid + SKIN_OFFSET
    txids = tuple(first_texture + i for i in range(count))
    out = {
        fdid: mb.chunked(bytes(blob), sfid=(skin_id,), txid=txids, extra=()),
        skin_id: mb.skin(
            lookup=tuple(range(3 * count)),
            indices=tuple(range(3 * count)),
            sections=sections,
            batches=tuple((i, 0, i) for i in range(count)),
        ),
    }
    out.update({t: texture() for t in txids})
    return out


def files_for(
    plan: renderplan.RenderPlan,
    attachments: tuple[Point, ...] = ATTACHMENTS,
    *,
    fixture: bool = False,
) -> dict[int, bytes]:
    """A constructed file for every id the plan names. `fixture`: the body
    is `fixture_body_files` (`attachments` is not used)."""
    out = (
        fixture_body_files(plan.body.file_data_id)
        if fixture
        else body_files(plan.body.file_data_id, attachments)
    )
    for m in plan.item_models:
        out.update(item_files(m.file_data_id))
    for f in plan.files:
        if f.role in ("texture", "item texture"):
            out[f.file_data_id] = texture()
    for la in plan.layers:
        out[la.file_data_id] = texture()
    return out


class Source:
    """`charmodel.Source` over a dict: every id there is local, every other
    `not_local` (or the state `states` gives)."""

    def __init__(self, files: Mapping[int, bytes], states: Mapping[int, str] | None = None) -> None:
        self.files = dict(files)
        self.states = dict(states or {})
        self.reads: list[int] = []

    def stat(self, fdid: int, *, in_table: bool | None = None) -> casc.CascFileStat:
        state = self.states.get(fdid, "local" if fdid in self.files else "not_local")
        return casc.CascFileStat(
            fdid=fdid,
            state=state,
            words=casc.STATE_WORDS[state],  # type: ignore[arg-type]
        )

    def read(self, fdid: int) -> bytes:
        self.reads.append(fdid)
        if self.stat(fdid).state != "local":
            raise casc.FileUnavailable(f"file {fdid} is not local", fdid, "not_local")
        return self.files[fdid]
