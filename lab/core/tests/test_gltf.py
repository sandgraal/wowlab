"""`gltf`: a scene to `.glb` bytes (M15-11, docs/LAB_PLAN.md §17.6).

Every scene here is constructed (labelled `constructed`): the values are
chosen so each assertion is exact. Each file is re-read by `_glb_read`, a
stdlib reader written from the glTF 2.0 specification, not from the
writer. Nothing here reads or writes a file.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

import pytest

from wowlab_core import gltf, png

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _glb_read import read_glb

RED = png.encode(1, 1, b"\xff\x00\x00\xff")


def _mesh(name: str, shift: float = 0.0) -> gltf.GltfMesh:
    positions = [0.0, 0.0, 0.0, 1.0 + shift, 0.0, 0.0, 0.0, 2.0, -3.0, 1.0, 1.0, 1.0]
    return gltf.GltfMesh(
        name=name,
        positions=gltf.floats(positions),
        normals=gltf.floats([0.0, 1.0, 0.0] * 4),
        uvs=gltf.floats([0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0]),
        primitives=(
            gltf.GltfPrimitive(indices=gltf.uints([0, 1, 2]), material=0),
            gltf.GltfPrimitive(indices=gltf.uints([1, 3, 2]), material=1),
        ),
    )


def _scene() -> gltf.GltfScene:
    return gltf.GltfScene(
        nodes=(
            gltf.GltfNode(name="body", mesh=_mesh("body")),
            gltf.GltfNode(name="item 1", mesh=_mesh("item", 0.5), translation=(0.5, 1.5, -2.0)),
        ),
        materials=(
            gltf.GltfMaterial(name="skin", image=0),
            gltf.GltfMaterial(
                name="hair", image=None, alpha_mode="MASK", alpha_cutoff=0.5, double_sided=True
            ),
        ),
        images=(gltf.GltfImage(name="red", png=RED),),
    )


def test_a_scene_round_trips_through_a_stdlib_reader_constructed() -> None:
    data, facts = gltf.encode_glb(_scene())
    glb = read_glb(data)
    doc = glb.document
    assert [n["name"] for n in glb.nodes] == ["body", "item 1"]
    assert "translation" not in glb.nodes[0]
    assert glb.nodes[1]["translation"] == [0.5, 1.5, -2.0]
    assert len(doc["meshes"]) == 2 and all(len(m["primitives"]) == 2 for m in doc["meshes"])
    first = doc["meshes"][0]["primitives"][0]
    assert list(glb.accessor(first["indices"])) == [0, 1, 2]
    assert list(glb.accessor(first["attributes"]["POSITION"]))[:6] == [0, 0, 0, 1, 0, 0]
    assert doc["accessors"][first["attributes"]["POSITION"]]["min"] == [0.0, 0.0, -3.0]
    assert doc["accessors"][first["attributes"]["POSITION"]]["max"] == [1.0, 2.0, 1.0]
    assert [m["alphaMode"] for m in doc["materials"]] == ["OPAQUE", "MASK"]
    assert doc["materials"][1]["alphaCutoff"] == 0.5 and doc["materials"][1]["doubleSided"]
    assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}
    assert "baseColorTexture" not in doc["materials"][1]["pbrMetallicRoughness"]
    assert glb.image(0) == RED
    assert facts.bytes == len(data)
    assert (facts.nodes, facts.meshes, facts.primitives) == (2, 2, 4)
    assert (facts.materials, facts.images, facts.vertices, facts.triangles) == (2, 1, 8, 4)
    assert facts.json_md5 == hashlib.md5(glb.json_bytes).hexdigest()
    assert facts.bin_md5 == hashlib.md5(glb.binary).hexdigest()


def test_the_same_scene_gives_the_same_bytes_constructed() -> None:
    assert gltf.encode_glb(_scene())[0] == gltf.encode_glb(_scene())[0]


def test_z_up_becomes_y_up_constructed() -> None:
    """[verify] on the signs (the owner's viewer check): the game's up (z)
    is glTF's up (y); the game's y becomes -z, a rotation, not a mirror."""
    assert gltf.to_y_up(1.0, 2.0, 3.0) == (1.0, 3.0, -2.0)
    a, b, c = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    ra, rb, rc = (gltf.to_y_up(*v) for v in (a, b, c))
    det = (
        ra[0] * (rb[1] * rc[2] - rb[2] * rc[1])
        - ra[1] * (rb[0] * rc[2] - rb[2] * rc[0])
        + ra[2] * (rb[0] * rc[1] - rb[1] * rc[0])
    )
    assert det == 1.0


def test_chunks_are_padded_to_four_bytes_constructed() -> None:
    data, _ = gltf.encode_glb(_scene())
    json_len = struct.unpack_from("<I", data, 12)[0]
    assert json_len % 4 == 0 and len(data) % 4 == 0


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"positions": b"\0" * 13}, "position bytes"),
        ({"normals": b"\0" * 12}, "normal bytes"),
        ({"uvs": b"\0" * 8}, "texture-coordinate bytes"),
        ({"primitives": ()}, "no triangles"),
        ({"primitives": (gltf.GltfPrimitive(indices=gltf.uints([0, 1]), material=None),)}, "whole"),
        (
            {"primitives": (gltf.GltfPrimitive(indices=gltf.uints([0, 1, 4]), material=None),)},
            "past",
        ),
        (
            {"primitives": (gltf.GltfPrimitive(indices=gltf.uints([0, 1, 2]), material=5),)},
            "material 5",
        ),
        ({"positions": gltf.floats([float("nan")] * 12)}, "position"),
        ({"normals": gltf.floats([float("inf")] * 12)}, "a normal that is not a finite"),
        ({"uvs": gltf.floats([0.0] * 7 + [float("nan")])}, "texture coordinate that is not"),
    ],
    ids=[
        "ragged",
        "normals",
        "uvs",
        "empty",
        "half",
        "past",
        "material",
        "nan",
        "inf normal",
        "nan uv",
    ],
)
def test_a_scene_that_does_not_fit_is_refused_constructed(
    change: dict[str, object], words: str
) -> None:
    mesh = _mesh("bad").model_copy(update=change)
    scene = _scene().model_copy(update={"nodes": (gltf.GltfNode(name="bad", mesh=mesh),)})
    with pytest.raises(gltf.GltfError, match=words):
        gltf.encode_glb(scene)


def test_a_non_finite_alpha_cutoff_is_refused_constructed() -> None:
    bad = gltf.GltfMaterial(name="m", image=None, alpha_mode="MASK", alpha_cutoff=float("nan"))
    scene = _scene().model_copy(update={"materials": (bad, bad)})
    with pytest.raises(gltf.GltfError, match="alpha cutoff"):
        gltf.encode_glb(scene)


def test_a_scene_with_no_nodes_is_refused_constructed() -> None:
    with pytest.raises(gltf.GltfError, match="no nodes"):
        gltf.encode_glb(_scene().model_copy(update={"nodes": ()}))


def test_an_image_that_does_not_exist_is_refused_constructed() -> None:
    scene = _scene().model_copy(update={"images": ()})
    with pytest.raises(gltf.GltfError, match="image 0"):
        gltf.encode_glb(scene)


def test_a_file_past_the_cap_is_refused_constructed() -> None:
    with pytest.raises(gltf.GltfError, match="cap"):
        gltf.encode_glb(_scene(), limit=100)
