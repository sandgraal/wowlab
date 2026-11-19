"""charmodel: a render plan drawn as one glTF scene, from the install's own
files (docs/LAB_PLAN.md §17.6, M15-11).

Input: a `renderplan.RenderPlan` made with a statter (each file's state in
`plan.files`) and a `Source` (`casc.CascSource` is one) that gives a file's
bytes and state by file data id. Output: a `CharModel`, the `.glb` bytes
and the PNG images beside it, as bytes, with what was drawn and what was
not. This module opens no file and writes nothing; `cascfiles` says where
the bytes go (never an install, L1).

**What is drawn.**

- **The body.** The body M2 (`plan.body.file_data_id`) and its first skin,
  the view skin (`SFID`'s first id; the LOD skins after it are not read:
  whether their lookup is relative to their vertex base is **[verify]**).
  A section is drawn when `renderplan.visible_geosets` shows its geoset id.
  The vertices are the M2's as stored: the bind pose, the rest pose, not
  the stance the client shows (no bone is read, §17.3).
- **Materials.** Each drawn section takes the first batch that names it
  (the skin's order); further batches of a section (glow and other
  layers) are not drawn and are counted. The batch's texture combo names
  an M2 texture; by its type: 1 the body texture (`compositor.compose`,
  drawn opaque whatever its alpha holds: the composite's alpha is what the
  blend function leaves, not a transparency); 6 hair and 19 eyes, the
  customization's separate textures (`compositor.separate_layers`); 2 the
  cape, the back item's texture when it has no model; 0 the file `TXID`
  names; any other type (15 to 18, the guild tabard's, which the Lab has
  no data for) a plain grey material. A separate texture with more than one
  layer (the eyes in layouts 105 and 111) is drawn from its first by
  `Layer`, then element id; the others are listed, not composed. The
  material's alpha follows the M2 material's blending mode: 0 opaque, 1
  alpha-keyed at 224/255, any other blended; flag `0x4` draws both faces
  (both **[verify]**, wow.export's reading).
- **Items.** Each `plan.item_models` entry of kind "attached" is drawn at
  the body's attachment of that id, its position as stored (the bind pose;
  no rotation, **[verify]**). An attachment stored at (0, 0, 0), the
  model's origin at its feet, is not placed and is listed (`at_origin`):
  observed on both fixture body models at 1.60.1.70959, the points for
  held items (0, 1 and 2) are stored there, on bones whose pivot is the
  origin too (`macos-70959/derived/charmodel.json`); the game presumably
  places them with the character's animation, which this wave does not
  read (**[verify]**). The head, shoulder and back points recorded are
  stored away from the origin and are used as stored (**[verify]**: no
  fixture character wears a model there, so none has been drawn).
  Kind "collection" at the origin, its
  sections filtered by the same visible geosets (**[verify]**); kind "no
  attachment known" is not placed and is listed. An item model's texture
  of type 2 is the item's texture of the same index; type 0 is its `TXID`.
- **Files that are not local.** The body model or its view skin not local
  (or unreadable) ends the run (`BodyNotDrawnError`, the file named). Any other
  file (an item model, a texture, a layer or piece of the body texture) is
  listed with its state and left out: its model is not placed, its
  material is grey, its layer is not painted.

**The body texture's budget.** `compose` caps its work at
`compositor.MAX_WORK_CANVASES` (64 canvases, about four minutes of Python
at the worst blend). A command should not wait that long, so `build` paints
at most `work_canvases` canvases' area (default `WORK_CANVASES`, 16: the
fixture characters paint about 2.2, the busiest recorded body layout about
11); a plan past it is drawn with a grey body and says so.

**What one model may hold.** Each section and each item model can name a
texture of its own, and sections are capped only at `m2.MAX_ENTRIES`, so
the scene keeps running totals and checks them before each texture is
read and decoded: at most `MAX_IMAGES` (64) distinct pictures, at most
`MAX_DECODED_PIXELS` texture pixels decoded (summed from each header), and
at most `MAX_SCENE_BYTES` (256 MiB) of PNG, vertex and index bytes. A
texture past a cap is left out (grey) with the reason in the report; an
item model whose shape would pass the byte cap is not placed; a body that
would is `BodyNotDrawnError`. `gltf.encode_glb`'s own cap is never the
first to trip.

What the result may say, and may not: it lists what was painted and drawn;
it never says the model looks as the character does in game (`LIKENESS`).
"""

from __future__ import annotations

import time
from array import array
from collections.abc import Callable, Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import blp, compositor, gltf, m2, png, renderplan
from wowlab_core.casc import CascError, CascFileStat
from wowlab_core.errors import WowlabError

__all__ = [
    "LIKENESS",
    "MAX_DECODED_PIXELS",
    "MAX_IMAGES",
    "MAX_SCENE_BYTES",
    "REST_POSE",
    "STAND_IN",
    "WORK_CANVASES",
    "BodyNotDrawnError",
    "BodyTextureFacts",
    "CharModel",
    "CharModelError",
    "FileLeftOut",
    "ModelDrawn",
    "PngOut",
    "Source",
    "build",
    "stand_in_words",
    "state_words",
]

WORK_CANVASES = 16
"""The most canvas area, in canvases, `build` lets one body texture paint."""

MAX_IMAGES = 64
"""The most distinct pictures one model holds (the fixture characters: 5 and
6). A texture past it is left out, its sections grey, before it is read."""
MAX_DECODED_PIXELS = 64 * 1024 * 1024
"""The most texture pixels one model decodes, summed (four 4096 x 4096
textures; the fixture characters decode under a million). Checked from each
texture's header before it is decoded."""
MAX_SCENE_BYTES = 256 * 1024 * 1024
"""The most bytes one model's pictures (PNG) and shapes (vertices and
indices) take, summed (the fixture characters: about 1 MB). A texture is not
decoded once the total reaches it; a model whose shape would pass it is not
drawn. So the file's own cap (`gltf.MAX_FILE_BYTES`) is never the first to
trip."""
_VERTEX_BYTES = 32  # POSITION 12, NORMAL 12, TEXCOORD_0 8
_INDEX_BYTES = 4
_TOO_MANY_PICTURES = (
    "left out: the model has more pictures than wowlab draws in one model "
    f"(at most {MAX_IMAGES}), so its parts are grey"
)
_TOO_MANY_PIXELS = (
    "left out: the model's pictures are larger in total than wowlab decodes for one model, "
    "so its parts are grey"
)
_TOO_MANY_BYTES = (
    "left out: the model's pictures and shapes already fill what wowlab writes in one file, "
    "so its parts are grey"
)

LIKENESS = (
    "This is not a picture of how the character looks in game: the order the body texture's "
    "layers and items are painted in, and how they are blended, follow a community tool and "
    "are not confirmed, and textures are enlarged without smoothing."
)
"""The one sentence the text output and `--json` both carry."""

REST_POSE = (
    "The model is in its rest pose, the pose the game's model file stores, not the stance the "
    "character takes in game."
)

STAND_IN: dict[str, str] = {
    "no record": (
        "No barber-shop record: the look is a stand-in (each option's first choice), not the "
        "character's look."
    ),
    "other body": (
        "The barber-shop record is for another race or body type, so it is not used: the look "
        "is a stand-in (each option's first choice), not the character's look."
    ),
}
"""The sentence for a stand-in look, by `renderplan.LookAsOf.stand_in`."""


def stand_in_words(look: renderplan.LookAsOf) -> str | None:
    """The stand-in sentence for `look`, or None when it is the record's."""
    if look.source == "record":
        return None
    return STAND_IN[look.stand_in or "no record"]


_TYPE_BODY = compositor.BODY_TEXTURE_TYPE
_TYPE_CAPE = 2
_TYPE_FILE = 0
_SEPARATE_NAMES = {6: "hair", 19: "eyes"}
_TWO_SIDED = 0x4
_ALPHA_KEY = 224 / 255
_ITEM_NODE = "item"


class CharModelError(WowlabError):
    """The model cannot be built."""


class BodyNotDrawnError(CharModelError):
    """The body model or its view skin cannot be read: not local, encrypted,
    or not a model this reader reads. `file_data_id` is the file."""

    def __init__(self, message: str, file_data_id: int) -> None:
        super().__init__(message)
        self.file_data_id = file_data_id


class Source(Protocol):
    """A file's bytes and its state, by file data id (`casc.CascSource`)."""

    def read(self, fdid: int) -> bytes | bytearray | memoryview: ...

    def stat(self, fdid: int, *, in_table: bool | None = None) -> CascFileStat: ...


def state_words(stat: CascFileStat) -> str:
    """A file's state in words, as `casc stat` says it: a file not in the
    client's local files carries "(part of this game version, but not on
    this computer)"."""
    if stat.state == "not_local":
        return f"{stat.words} (part of this game version, but not on this computer)"
    return stat.words


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FileLeftOut(_Frozen):
    """A file the plan names that is not drawn: its `state` (§17.1's words
    in `words`), and what it was for."""

    file_data_id: int
    what: str
    state: str
    words: str
    reason: str


class ModelDrawn(_Frozen):
    """One model of the plan: the body or an item's. `placed` False: not in
    the scene, `reason` says why. `sections` the view skin's, `drawn` the
    ones shown; `extra_batches` the further batches of drawn sections, not
    drawn. `at_origin`: not placed because the body model stores its
    attachment point at the origin (the hand points read so far)."""

    what: Literal["body", "item"]
    slot: int | None = None
    item_id: int | None = None
    file_data_id: int
    skin_file_data_id: int | None = None
    kind: Literal["body", "attached", "collection", "no attachment known"]
    attachment_id: int | None = None
    position: tuple[float, float, float] | None = None
    placed: bool
    reason: str | None = None
    at_origin: bool = False
    sections: int = 0
    drawn: int = 0
    extra_batches: int = 0
    vertices: int = 0
    triangles: int = 0


class PngOut(_Frozen):
    """A texture written beside the `.glb` as a PNG: `name` the part of the
    file name after the character's (`body`, `hair`, `eyes`, or
    `texture<type>`), the M2 texture type, the file it was decoded from
    (None for the body texture, which is composed), and the files of the
    same texture type not drawn."""

    name: str
    texture_type: int
    file_data_id: int | None
    width: int
    height: int
    png: bytes = Field(repr=False)
    not_drawn: tuple[int, ...] = ()


class BodyTextureFacts(_Frozen):
    """The body texture: what was painted, in order, and what was not.
    `composed` False: drawn grey, `why` says why."""

    composed: bool
    why: str | None = None
    width: int | None = None
    height: int | None = None
    painted: tuple[compositor.PaintedLayer, ...] = ()
    not_painted: tuple[compositor.NotPainted, ...] = ()


class CharModel(_Frozen):
    """The scene as `.glb` bytes, the PNGs beside it, and what they hold."""

    glb: bytes = Field(repr=False)
    facts: gltf.GlbFacts
    pngs: tuple[PngOut, ...]
    models: tuple[ModelDrawn, ...]
    body_texture: BodyTextureFacts
    left_out: tuple[FileLeftOut, ...]
    notes: tuple[str, ...]
    seconds: dict[str, float]
    held_bytes: int = 0  # PNG, vertex and index bytes, against MAX_SCENE_BYTES
    decoded_pixels: int = 0  # texture pixels decoded, against MAX_DECODED_PIXELS


# ─── reading ─────────────────────────────────────────────────────────────────


class _Files:
    """Each file's state, from the plan or asked of the source once, and
    the textures decoded so far, by id."""

    def __init__(self, plan: renderplan.RenderPlan, source: Source) -> None:
        self.source = source
        self.stats: dict[int, CascFileStat] = {
            f.file_data_id: f.stat for f in plan.files if f.stat is not None
        }
        self.left_out: dict[int, FileLeftOut] = {}

    def stat(self, fdid: int) -> CascFileStat:
        got = self.stats.get(fdid)
        if got is None:
            got = self.source.stat(fdid)
            self.stats[fdid] = got
        return got

    def leave_out(self, fdid: int, what: str, reason: str, *, unreadable: bool = False) -> None:
        """List `fdid` as not drawn. `unreadable`: a local file that could
        not be read or decoded, listed as "unreadable", not "local"."""
        if fdid in self.left_out:
            return
        stat = self.stats.get(fdid)
        state, words = "unreadable", "could not be read"
        if stat is not None and not unreadable:
            state, words = stat.state, state_words(stat)
        self.left_out[fdid] = FileLeftOut(
            file_data_id=fdid, what=what, state=state, words=words, reason=reason
        )

    def local(self, fdid: int, what: str) -> bool:
        stat = self.stat(fdid)
        if stat.state == "local":
            return True
        self.leave_out(fdid, what, f"file {fdid} is {state_words(stat)}")
        return False

    def read(self, fdid: int, what: str) -> memoryview | None:
        if not self.local(fdid, what):
            return None
        try:
            return memoryview(self.source.read(fdid)).cast("B")
        except CascError as exc:
            self.leave_out(fdid, what, f"file {fdid} could not be read: {exc}", unreadable=True)
            return None


def _body(files: _Files, plan: renderplan.RenderPlan) -> tuple[m2.M2Model, int, m2.Skin]:
    fdid = plan.body.file_data_id
    stat = files.stat(fdid)
    if stat.state != "local":
        raise BodyNotDrawnError(f"the body model, file {fdid}, is {state_words(stat)}", fdid)
    try:
        model = m2.parse_m2(files.source.read(fdid))
    except (CascError, m2.M2Error) as exc:
        raise BodyNotDrawnError(
            f"the body model, file {fdid}, cannot be read: {exc}", fdid
        ) from exc
    if not model.skin_file_data_ids:
        raise BodyNotDrawnError(f"the body model, file {fdid}, names no skin", fdid)
    skin_id = model.skin_file_data_ids[0]
    stat = files.stat(skin_id)
    if stat.state != "local":
        raise BodyNotDrawnError(
            f"the body model's skin, file {skin_id}, is {state_words(stat)}", skin_id
        )
    try:
        skin = m2.parse_skin(files.source.read(skin_id), vertex_count=model.vertices.count)
    except (CascError, m2.M2Error) as exc:
        raise BodyNotDrawnError(
            f"the body model's skin, file {skin_id}, cannot be read: {exc}", skin_id
        ) from exc
    return model, skin_id, skin


# ─── the body texture ────────────────────────────────────────────────────────


def _local_plan(files: _Files, plan: renderplan.RenderPlan) -> renderplan.RenderPlan:
    """The plan without the layers and pieces whose file is not local (each
    listed)."""
    layers = tuple(
        la for la in plan.layers if files.local(la.file_data_id, "a customization texture")
    )
    pieces = tuple(
        p
        for p in plan.item_pieces
        if files.local(p.file_data_id, f"a piece of item {p.item_id} on the body texture")
    )
    return plan.model_copy(update={"layers": layers, "item_pieces": pieces})


def _body_texture(
    plan: renderplan.RenderPlan, source: Source, work_canvases: int
) -> tuple[BodyTextureFacts, compositor.BodyTexture | None]:
    try:
        jobs, _ = compositor.paint_jobs(plan)
        width, height = plan.body.layout_width or 0, plan.body.layout_height or 0
        area = sum(j.width * j.height for j in jobs)
        if area > work_canvases * width * height:
            return (
                BodyTextureFacts(
                    composed=False,
                    why=f"painting it would cover {area / (width * height):.1f} times the "
                    f"texture's area; wowlab stops at {work_canvases}",
                ),
                None,
            )
        result = compositor.compose(plan, source)
    except (compositor.CompositorError, blp.BlpError, CascError) as exc:
        return BodyTextureFacts(composed=False, why=f"the body texture cannot be made: {exc}"), None
    return (
        BodyTextureFacts(
            composed=True,
            width=result.width,
            height=result.height,
            painted=result.painted,
            not_painted=result.not_painted,
        ),
        result,
    )


# ─── the scene ───────────────────────────────────────────────────────────────


class _SceneFullError(CharModelError):
    """A model's shape would pass `MAX_SCENE_BYTES`."""


class _Scene:
    def __init__(self, files: _Files) -> None:
        self.files = files
        self.images: list[gltf.GltfImage] = []
        self.materials: list[gltf.GltfMaterial] = []
        self._material_of: dict[tuple[object, ...], int] = {}
        self.image_of: dict[object, int | None] = {}
        self.pngs: list[PngOut] = []
        self.notes: list[str] = []
        self.bytes = 0  # PNG, vertex and index bytes held so far
        self.pixels = 0  # texture pixels decoded so far

    def take(self, count: int) -> bool:
        """Add `count` bytes to the running total, or False (nothing
        added) when that would pass `MAX_SCENE_BYTES`."""
        if self.bytes + count > MAX_SCENE_BYTES:
            return False
        self.bytes += count
        return True

    def image_png(self, key: object, name: str, data: bytes) -> int:
        self.bytes += len(data)
        self.images.append(gltf.GltfImage(name=name, png=data))
        self.image_of[key] = len(self.images) - 1
        return len(self.images) - 1

    def texture_file(self, fdid: int, what: str) -> int | None:
        """The image of a texture file, decoded once; None when it cannot be."""
        if fdid in self.image_of:
            return self.image_of[fdid]
        self.image_of[fdid] = None
        # The caps, before anything is read or decoded.
        if len(self.images) >= MAX_IMAGES:
            self.files.leave_out(fdid, what, _TOO_MANY_PICTURES)
            return None
        if self.bytes >= MAX_SCENE_BYTES:
            self.files.leave_out(fdid, what, _TOO_MANY_BYTES)
            return None
        data = self.files.read(fdid, what)
        if data is None:
            return None
        try:
            header = blp.parse_header(data)
            pixels = header.width * header.height
            if self.pixels + pixels > MAX_DECODED_PIXELS:
                self.files.leave_out(fdid, what, _TOO_MANY_PIXELS)
                return None
            self.pixels += pixels
            image = blp.decode(data)
            del data
            encoded = png.encode(image.width, image.height, image.rgba)
        except (blp.BlpError, png.PngError) as exc:
            self.files.leave_out(
                fdid, what, f"file {fdid} is not a texture wowlab reads: {exc}", unreadable=True
            )
            return None
        if self.bytes + len(encoded) > MAX_SCENE_BYTES:
            self.files.leave_out(fdid, what, _TOO_MANY_BYTES)
            return None
        return self.image_png(fdid, f"file {fdid}", encoded)

    def material(
        self, image: int | None, name: str, blending: int, flags: int, opaque: bool
    ) -> int:
        if opaque or blending == 0:
            mode: Literal["OPAQUE", "MASK", "BLEND"] = "OPAQUE"
        elif blending == 1:
            mode = "MASK"
        else:
            mode = "BLEND"
        two = bool(flags & _TWO_SIDED)
        key = (image, mode, two)
        if key not in self._material_of:
            self.materials.append(
                gltf.GltfMaterial(
                    name=name,
                    image=image,
                    alpha_mode=mode,
                    alpha_cutoff=_ALPHA_KEY if mode == "MASK" else None,
                    double_sided=two,
                )
            )
            self._material_of[key] = len(self.materials) - 1
        return self._material_of[key]


def _mesh(
    name: str,
    model: m2.M2Model,
    skin: m2.Skin,
    visible: set[int] | None,
    texture_of: Callable[[int], tuple[int | None, bool]],
    scene: _Scene,
) -> tuple[gltf.GltfMesh | None, int, int, int, int]:
    """The mesh of the skin's shown sections (all when `visible` is None):
    (mesh or None when nothing is shown, sections drawn, extra batches,
    vertices, triangles). `texture_of`: an M2 texture index's image and
    whether it is drawn opaque, asked only for the textures drawn."""
    first: dict[int, m2.SkinBatch] = {}
    more: dict[int, int] = {}
    for each in skin.batches:
        if each.section_index in first:
            more[each.section_index] = more.get(each.section_index, 0) + 1
        else:
            first[each.section_index] = each
    extra = 0
    parts: dict[int, list[array[int]]] = {}
    drawn = 0
    for section in skin.sections:
        if visible is not None and section.geoset_id not in visible:
            continue
        batch = first.get(section.index)
        if batch is None or section.index_count == 0:
            continue
        drawn += 1
        extra += more.get(section.index, 0)
        image: int | None = None
        opaque = False
        if batch.texture_combo_index < len(model.texture_combos):
            tex = model.texture_combos[batch.texture_combo_index]
            image, opaque = texture_of(tex)
        blending, flags = 0, 0
        if batch.material_index < len(model.materials):
            mat = model.materials[batch.material_index]
            blending, flags = mat.blending_mode, mat.flags
        label = f"{name} texture" if image is not None else f"{name}, no texture"
        material = scene.material(image, label, blending, flags, opaque)
        parts.setdefault(material, []).append(skin.triangles(section))
    if not parts:
        return None, drawn, extra, 0, 0
    used = sorted(set().union(*(set(tris) for lists in parts.values() for tris in lists)))
    count = sum(len(t) for lists in parts.values() for t in lists)
    if not scene.take(len(used) * _VERTEX_BYTES + count * _INDEX_BYTES):
        raise _SceneFullError(name)
    remap: dict[int, int] = {v: i for i, v in enumerate(used)}
    verts = model.vertices
    pos, nrm, uv = verts.positions, verts.normals, verts.uv
    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    for v in used:
        positions += gltf.to_y_up(pos[3 * v], pos[3 * v + 1], pos[3 * v + 2])
        normals += gltf.to_y_up(nrm[3 * v], nrm[3 * v + 1], nrm[3 * v + 2])
        uvs += (uv[2 * v], uv[2 * v + 1])
    primitives = []
    triangles = 0
    for material, lists in parts.items():
        indices = [remap[v] for tris in lists for v in tris]
        triangles += len(indices) // 3
        primitives.append(gltf.GltfPrimitive(indices=gltf.uints(indices), material=material))
    mesh = gltf.GltfMesh(
        name=name,
        positions=gltf.floats(positions),
        normals=gltf.floats(normals),
        uvs=gltf.floats(uvs),
        primitives=tuple(primitives),
    )
    return mesh, drawn, extra, len(used), triangles


def _separate(plan: renderplan.RenderPlan, scene: _Scene) -> dict[int, int | None]:
    """Each separate texture type's image (hair 6, eyes 19, ...): its first
    layer by `Layer`, then element id, decoded; the others listed."""
    by_type: dict[int, list[renderplan.TextureLayer]] = {}
    for la in compositor.separate_layers(plan):
        by_type.setdefault(la.texture_type, []).append(la)
    out: dict[int, int | None] = {}
    for kind, layers in sorted(by_type.items()):
        layers.sort(key=lambda la: (la.layer, la.element_id))
        name = _SEPARATE_NAMES.get(kind, f"texture{kind}")
        drawn = layers[0]
        image = scene.texture_file(drawn.file_data_id, f"the {name} texture")
        out[kind] = image
        if image is None:
            continue
        others = tuple(la.file_data_id for la in layers[1:])
        decoded = scene.images[image]
        width, height = _png_size(decoded.png)
        scene.pngs.append(
            PngOut(
                name=name,
                texture_type=kind,
                file_data_id=drawn.file_data_id,
                width=width,
                height=height,
                png=decoded.png,
                not_drawn=others,
            )
        )
        if others:
            scene.notes.append(
                f"the {name} texture has {len(layers)} layers; the first (file "
                f"{drawn.file_data_id}) is drawn and the others are not combined with it: "
                + ", ".join(f"file {f}" for f in others)
            )
    return out


def _png_size(data: bytes) -> tuple[int, int]:
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _cape(plan: renderplan.RenderPlan) -> int | None:
    return next((t.file_data_id for t in plan.item_textures if t.target == "cape"), None)


def _item_textures(plan: renderplan.RenderPlan, item: renderplan.ItemModel) -> int | None:
    return next(
        (
            t.file_data_id
            for t in plan.item_textures
            if t.slot == item.slot
            and t.item_display_info_id == item.item_display_info_id
            and t.index == item.index
            and t.target == "item model"
        ),
        None,
    )


def _texture_map(
    model: m2.M2Model,
    scene: _Scene,
    *,
    body: int | None,
    separate: Mapping[int, int | None],
    object_skin: int | None,
    what: str,
) -> Callable[[int], tuple[int | None, bool]]:
    """An M2 texture index's image (None: grey) and whether it is drawn
    opaque; a texture file is read and decoded the first time a drawn
    section asks for it, never for a texture no drawn section uses."""

    def texture_of(index: int) -> tuple[int | None, bool]:
        if not 0 <= index < len(model.textures):
            return None, False
        tex = model.textures[index]
        if tex.type == _TYPE_BODY:
            return body, True
        if tex.type in separate:
            return separate[tex.type], False
        if tex.type == _TYPE_CAPE and object_skin is not None:
            return scene.texture_file(object_skin, f"{what}'s texture"), False
        if tex.type == _TYPE_FILE and tex.file_data_id:
            return scene.texture_file(tex.file_data_id, f"{what}'s texture"), False
        return None, False

    return texture_of


def _item_drawn(item: renderplan.ItemModel, **rest: object) -> ModelDrawn:
    return ModelDrawn.model_validate(
        {
            "what": "item",
            "slot": item.slot,
            "item_id": item.item_id,
            "file_data_id": item.file_data_id,
            "kind": item.kind,
            "attachment_id": item.attachment_id,
            **rest,
        }
    )


def _item_node(
    plan: renderplan.RenderPlan,
    item: renderplan.ItemModel,
    body: m2.M2Model,
    files: _Files,
    scene: _Scene,
) -> tuple[ModelDrawn, gltf.GltfNode | None]:
    what = f"item {item.item_id}"
    if item.kind == "no attachment known":
        reason = (
            "the game's tables do not say where on the body this model goes, so it is not placed"
        )
        return _item_drawn(item, placed=False, reason=reason), None
    translation = (0.0, 0.0, 0.0)
    if item.kind == "attached":
        point = body.attachment(item.attachment_id) if item.attachment_id is not None else None
        if point is None:
            reason = (
                f"the body model lacks the point this item is held at ({item.attachment_id}), "
                "so it has no place for it"
            )
            return _item_drawn(item, placed=False, reason=reason), None
        if not any(point.position):
            # The model's origin, at the feet: the game moves this point
            # with the character's animation, which is not read (§17.6 note).
            reason = (
                f"the body model stores attachment point {item.attachment_id} at its origin, so it "
                "has no place in the rest pose; the item is not drawn"
            )
            return _item_drawn(item, placed=False, at_origin=True, reason=reason), None
        translation = gltf.to_y_up(*point.position)
    data = files.read(item.file_data_id, f"the model of {what}")
    if data is None:
        reason = files.left_out[item.file_data_id].reason
        return _item_drawn(item, placed=False, reason=reason), None
    try:
        model = m2.parse_m2(data)
        if not model.skin_file_data_ids:
            raise m2.M2MalformedError("the model names no skin")
        skin_id = model.skin_file_data_ids[0]
        skin_data = files.read(skin_id, f"the skin of {what}'s model")
        if skin_data is None:
            reason = files.left_out[skin_id].reason
            return _item_drawn(item, skin_file_data_id=skin_id, placed=False, reason=reason), None
        skin = m2.parse_skin(skin_data, vertex_count=model.vertices.count)
    except m2.M2Error as exc:
        reason = f"not a model wowlab reads: {exc}"
        files.leave_out(item.file_data_id, f"the model of {what}", reason, unreadable=True)
        return _item_drawn(item, placed=False, reason=reason), None
    textures = _texture_map(
        model,
        scene,
        body=None,
        separate={},
        object_skin=_item_textures(plan, item),
        what=what,
    )
    visible = (
        set(renderplan.visible_geosets(plan.geosets, (s.geoset_id for s in skin.sections)))
        if item.kind == "collection"
        else None
    )
    try:
        mesh, drawn, extra, vertices, triangles = _mesh(
            f"{_ITEM_NODE} {item.item_id}", model, skin, visible, textures, scene
        )
    except _SceneFullError:
        reason = (
            "left out: the model's pictures and shapes already fill what wowlab writes in one file"
        )
        return _item_drawn(item, skin_file_data_id=skin_id, placed=False, reason=reason), None
    done = _item_drawn(
        item,
        skin_file_data_id=skin_id,
        position=translation if item.kind == "attached" else None,
        placed=mesh is not None,
        reason=None if mesh is not None else "no section of its model is shown",
        sections=len(skin.sections),
        drawn=drawn,
        extra_batches=extra,
        vertices=vertices,
        triangles=triangles,
    )
    if mesh is None:
        return done, None
    return done, gltf.GltfNode(
        name=f"{_ITEM_NODE} {item.item_id} (slot {item.slot})", mesh=mesh, translation=translation
    )


def build(
    plan: renderplan.RenderPlan,
    source: Source,
    *,
    work_canvases: int = WORK_CANVASES,
    limit: int = gltf.MAX_FILE_BYTES,
) -> CharModel:
    """The plan drawn: the body (or `BodyNotDrawnError`), its texture, the
    separate textures and the placed item models, as one `.glb` and its
    PNGs (the module docstring)."""
    seconds: dict[str, float] = {}
    clock = time.perf_counter()
    files = _Files(plan, source)
    body, skin_id, skin = _body(files, plan)
    seconds["body_model"] = time.perf_counter() - clock

    clock = time.perf_counter()
    painted_plan = _local_plan(files, plan)
    texture_facts, composite = _body_texture(painted_plan, source, work_canvases)
    seconds["body_texture"] = time.perf_counter() - clock

    clock = time.perf_counter()
    scene = _Scene(files)
    body_image: int | None = None
    if composite is not None:
        encoded = composite.png()
        body_image = scene.image_png("body", "body texture", encoded)
        scene.pngs.append(
            PngOut(
                name="body",
                texture_type=_TYPE_BODY,
                file_data_id=None,
                width=composite.width,
                height=composite.height,
                png=encoded,
            )
        )
    separate = _separate(painted_plan, scene)
    textures = _texture_map(
        body,
        scene,
        body=body_image,
        separate=separate,
        object_skin=_cape(plan),
        what="the body",
    )
    available = [s.geoset_id for s in skin.sections]
    visible = set(renderplan.visible_geosets(plan.geosets, available))
    try:
        mesh, drawn, extra, vertices, triangles = _mesh(
            "body", body, skin, visible, textures, scene
        )
    except _SceneFullError as exc:
        raise BodyNotDrawnError(
            f"the body model, file {plan.body.file_data_id}, is larger than wowlab writes in one "
            "file",
            plan.body.file_data_id,
        ) from exc
    if mesh is None:
        raise BodyNotDrawnError(
            f"no section of the body model, file {plan.body.file_data_id}, is shown",
            plan.body.file_data_id,
        )
    models = [
        ModelDrawn(
            what="body",
            file_data_id=plan.body.file_data_id,
            skin_file_data_id=skin_id,
            kind="body",
            placed=True,
            sections=len(skin.sections),
            drawn=drawn,
            extra_batches=extra,
            vertices=vertices,
            triangles=triangles,
        )
    ]
    nodes = [gltf.GltfNode(name="body", mesh=mesh)]
    for item in plan.item_models:
        done, node = _item_node(plan, item, body, files, scene)
        models.append(done)
        if node is not None:
            nodes.append(node)
    seconds["models"] = time.perf_counter() - clock

    clock = time.perf_counter()
    glb, facts = gltf.encode_glb(
        gltf.GltfScene(
            nodes=tuple(nodes), materials=tuple(scene.materials), images=tuple(scene.images)
        ),
        limit=limit,
    )
    seconds["glb"] = time.perf_counter() - clock
    return CharModel(
        glb=glb,
        facts=facts,
        pngs=tuple(scene.pngs),
        models=tuple(models),
        body_texture=texture_facts,
        left_out=tuple(files.left_out.values()),
        notes=tuple(dict.fromkeys(scene.notes)),
        seconds=seconds,
        held_bytes=scene.bytes,
        decoded_pixels=scene.pixels,
    )
