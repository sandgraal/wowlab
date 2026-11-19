"""A stdlib reader of glTF 2.0 binary files for the `gltf` and `char model`
tests (M15-11). Written from the glTF 2.0 specification, not from
`wowlab_core.gltf`: it checks what a viewer relies on and returns what the
tests assert.

Checked: the 12-byte header (`glTF`, version 2, the total length equal to
the file's), the JSON chunk first and the BIN chunk second, each a multiple
of four bytes; the buffer's length within the BIN chunk; every buffer view
inside the buffer and on a four-byte boundary; every accessor inside its
view, with the component type and type the attribute needs (POSITION and
NORMAL float VEC3, TEXCOORD_0 float VEC2, indices uint32 SCALAR), the
accessors of one primitive agreeing on the vertex count, POSITION's `min`
and `max` equal to its values' bounds, and every index below the vertex
count; every material's texture, sampler and image named; every image a
PNG by its signature; every node's mesh named and every scene node named.
"""

from __future__ import annotations

import json
import struct
from array import array
from dataclasses import dataclass, field
from typing import Any

FLOAT, UINT = 5126, 5125
PNG = b"\x89PNG\r\n\x1a\n"
SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}


@dataclass
class Glb:
    document: dict[str, Any]
    binary: bytes
    json_bytes: bytes
    nodes: list[dict[str, Any]] = field(default_factory=list)

    def accessor(self, index: int) -> array[Any]:
        acc = self.document["accessors"][index]
        view = self.document["bufferViews"][acc["bufferView"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        width = SIZES[acc["type"]] * 4
        data = self.binary[start : start + acc["count"] * width]
        out = array("f" if acc["componentType"] == FLOAT else "I")
        out.frombytes(data)
        return out

    def image(self, index: int) -> bytes:
        view = self.document["bufferViews"][self.document["images"][index]["bufferView"]]
        start = view.get("byteOffset", 0)
        return self.binary[start : start + view["byteLength"]]


def read_glb(data: bytes) -> Glb:
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    assert (magic, version, total) == (b"glTF", 2, len(data))
    json_len, json_kind = struct.unpack_from("<II", data, 12)
    assert json_kind == 0x4E4F534A and json_len % 4 == 0
    json_bytes = data[20 : 20 + json_len]
    at = 20 + json_len
    bin_len, bin_kind = struct.unpack_from("<II", data, at)
    assert bin_kind == 0x004E4942 and bin_len % 4 == 0
    binary = data[at + 8 : at + 8 + bin_len]
    assert at + 8 + bin_len == total
    doc = json.loads(json_bytes.decode("utf-8"))
    assert doc["asset"]["version"] == "2.0"
    (buffer,) = doc["buffers"]
    assert "uri" not in buffer and buffer["byteLength"] <= bin_len
    for view in doc["bufferViews"]:
        start = view.get("byteOffset", 0)
        assert start % 4 == 0 and start + view["byteLength"] <= buffer["byteLength"]
    for acc in doc["accessors"]:
        view = doc["bufferViews"][acc["bufferView"]]
        assert acc["count"] * SIZES[acc["type"]] * 4 <= view["byteLength"]
    glb = Glb(doc, binary, json_bytes)
    for mesh in doc["meshes"]:
        for prim in mesh["primitives"]:
            attrs = prim["attributes"]
            pos = doc["accessors"][attrs["POSITION"]]
            assert (pos["componentType"], pos["type"]) == (FLOAT, "VEC3")
            nrm = doc["accessors"][attrs["NORMAL"]]
            uv = doc["accessors"][attrs["TEXCOORD_0"]]
            assert (nrm["componentType"], nrm["type"]) == (FLOAT, "VEC3")
            assert (uv["componentType"], uv["type"]) == (FLOAT, "VEC2")
            assert pos["count"] == nrm["count"] == uv["count"]
            values = glb.accessor(attrs["POSITION"])
            assert pos["min"] == [min(values[i::3]) for i in range(3)]
            assert pos["max"] == [max(values[i::3]) for i in range(3)]
            idx = doc["accessors"][prim["indices"]]
            assert (idx["componentType"], idx["type"]) == (UINT, "SCALAR")
            assert idx["count"] % 3 == 0
            assert max(glb.accessor(prim["indices"])) < pos["count"]
            assert prim.get("mode", 4) == 4
            if "material" in prim:
                assert 0 <= prim["material"] < len(doc["materials"])
    for material in doc.get("materials", []):
        tex = material["pbrMetallicRoughness"].get("baseColorTexture")
        if tex is not None:
            texture = doc["textures"][tex["index"]]
            assert 0 <= texture["sampler"] < len(doc["samplers"])
            assert 0 <= texture["source"] < len(doc["images"])
    for i, image in enumerate(doc.get("images", [])):
        assert image["mimeType"] == "image/png"
        assert glb.image(i).startswith(PNG)
    for node in doc["nodes"]:
        assert 0 <= node["mesh"] < len(doc["meshes"])
    assert sorted(doc["scenes"][doc["scene"]]["nodes"]) == list(range(len(doc["nodes"])))
    glb.nodes = doc["nodes"]
    return glb
