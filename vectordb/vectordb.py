from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    import chromadb
    from sentence_transformers import SentenceTransformer
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False


Collection = Literal["official_docs", "community_code", "error_patterns"]


@dataclass
class SearchResult:
    snippet_id: str
    code: str
    description: str
    tags: list[str]
    score: float
    collection: str


_RESET_SCENE = '''\
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
'''

_BOOLEAN_DIFFERENCE = '''\
import bpy
def boolean_difference(body, cutter):
    for obj in (body, cutter):
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.objects.active = body
    mod = body.modifiers.new(name="Bool", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    mod.solver = 'EXACT'
    bpy.ops.object.modifier_apply(modifier="Bool")
    bpy.data.objects.remove(cutter, do_unlink=True)
    return body
'''

_CYLINDER = '''\
import bpy
def add_cylinder(radius=1.0, depth=2.0, vertices=64, location=(0,0,0)):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, vertices=vertices, location=location)
    return bpy.context.active_object
'''

_SOLIDIFY = '''\
import bpy
def solidify_for_printing(obj, thickness_mm=2.0):
    mod = obj.modifiers.new(name="Solidify", type='SOLIDIFY')
    mod.thickness = thickness_mm / 1000.0
    mod.offset = -1.0
    mod.use_even_offset = True
    mod.fill_rim = True
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Solidify")
    return obj
'''

_WELD_NORMALS = '''\
import bpy
def repair_mesh(obj, merge_threshold=0.0001):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    weld = obj.modifiers.new(name="Weld", type='WELD')
    weld.merge_threshold = merge_threshold
    bpy.ops.object.modifier_apply(modifier="Weld")
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
'''

_EXPORT_STL = '''\
import bpy
from pathlib import Path
def export_stl(filename="output"):
    output_dir = Path.home() / "Desktop" / "blender_prints"
    output_dir.mkdir(parents=True, exist_ok=True)
    stl_path = output_dir / f"{filename}.stl"
    bpy.ops.export_mesh.stl(filepath=str(stl_path), use_selection=True, use_mesh_modifiers=True, global_scale=1000)
    print(f"STL_EXPORTED:{stl_path}")
    return stl_path
'''

_REMESH_VOXEL = '''\
import bpy
def remesh_voxel(obj, voxel_size=0.005):
    mod = obj.modifiers.new(name="Remesh", type='REMESH')
    mod.mode = 'VOXEL'
    mod.voxel_size = voxel_size
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Remesh")
    return obj
'''

_FILL_HOLES = '''\
import bpy
def fill_holes(obj):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.mesh.select_non_manifold(extend=False, use_boundary=True)
    bpy.ops.mesh.fill_holes(sides=0)
    bpy.ops.object.mode_set(mode='OBJECT')
'''

_BMESH_MERGE = '''\
import bpy, bmesh
def merge_doubles(obj, threshold=0.0001):
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=threshold)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
'''

_TEXT_OBJECT = '''\
import bpy
def create_text(text="3D", size=0.1, extrude=0.005):
    bpy.ops.object.text_add(location=(0,0,0))
    obj = bpy.context.active_object
    obj.data.body = text
    obj.data.size = size
    obj.data.extrude = extrude
    bpy.ops.object.convert(target='MESH')
    return bpy.context.active_object
'''

_DUPLICATE_OBJECT = '''\
import bpy
def duplicate(source, offset=(0,0,0)):
    bpy.ops.object.select_all(action='DESELECT')
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.duplicate()
    copy = bpy.context.active_object
    copy.location = (source.location.x + offset[0], source.location.y + offset[1], source.location.z + offset[2])
    return copy
'''

_DIAGNOSE_MESH = '''\
import bpy, bmesh
def diagnose_mesh(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    zf = sum(1 for f in bm.faces if f.calc_area() < 1e-8)
    lv = sum(1 for v in bm.verts if not v.link_edges)
    bm.free()
    return nm, zf, lv
'''

_SAFE_BOOLEAN = '''\
import bpy, bmesh
def safe_boolean(body, cutter):
    if body.type != 'MESH' or cutter.type != 'MESH':
        raise TypeError("Entrambi gli oggetti devono essere MESH")
    for obj in (body, cutter):
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.objects.active = body
    mod = body.modifiers.new(name="BoolSafe", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    mod.solver = 'EXACT'
    bpy.ops.object.modifier_apply(modifier="BoolSafe")
    bpy.data.objects.remove(cutter, do_unlink=True)
    return body
'''

_SPHERE = '''\
import bpy
def add_sphere(radius=0.05, segments=64, location=(0,0,0)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=segments, ring_count=segments//2, location=location)
    return bpy.context.active_object
'''

_CONE = '''\
import bpy
def add_cone(radius_bottom=0.05, depth=0.1, vertices=64, location=(0,0,0)):
    bpy.ops.mesh.primitive_cone_add(radius1=radius_bottom, depth=depth, vertices=vertices, location=location)
    return bpy.context.active_object
'''

_TORUS = '''\
import bpy
def add_torus(major_radius=0.05, minor_radius=0.01, major_segments=48, minor_segments=16, location=(0,0,0)):
    bpy.ops.mesh.primitive_torus_add(major_radius=major_radius, minor_radius=minor_radius, major_segments=major_segments, minor_segments=minor_segments, location=location)
    return bpy.context.active_object
'''

_ARRAY_MODIFIER = '''\
import bpy
def array_modifier(obj, count=5, offset=(0.06, 0, 0)):
    mod = obj.modifiers.new(name="Array", type='ARRAY')
    mod.count = count
    mod.relative_offset_displace = offset
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Array")
    return obj
'''

_MIRROR_MODIFIER = '''\
import bpy
def mirror_modifier(obj, axis_x=True, axis_y=False, axis_z=False, merge=True, clip_threshold=0.0001):
    mod = obj.modifiers.new(name="Mirror", type='MIRROR')
    mod.use_axis = (axis_x, axis_y, axis_z)
    mod.use_mirror_merge = merge
    mod.merge_threshold = clip_threshold
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Mirror")
    return obj
'''

_SCREW_MODIFIER = '''\
import bpy
def screw_modifier(obj, steps=64, render_steps=64, angle=6.28319, screw_offset=0.0, iterations=1):
    mod = obj.modifiers.new(name="Screw", type='SCREW')
    mod.steps = steps
    mod.render_steps = render_steps
    mod.angle = angle
    mod.screw_offset = screw_offset
    mod.iterations = iterations
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Screw")
    return obj
'''

_BEVEL_MODIFIER = '''\
import bpy
def bevel_modifier(obj, width=0.002, segments=3, limit_method='ANGLE', angle_limit=0.436332):
    mod = obj.modifiers.new(name="Bevel", type='BEVEL')
    mod.width = width
    mod.segments = segments
    mod.limit_method = limit_method
    mod.angle_limit = angle_limit
    mod.use_clamp_overlap = True
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Bevel")
    return obj
'''

_DECIMATE_MODIFIER = '''\
import bpy
def decimate_modifier(obj, ratio=0.5, use_collapse_triangulate=True):
    mod = obj.modifiers.new(name="Decimate", type='DECIMATE')
    mod.ratio = ratio
    mod.use_collapse_triangulate = use_collapse_triangulate
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Decimate")
    return obj
'''

_BOOLEAN_UNION = '''\
import bpy
def boolean_union(body, other):
    for obj in (body, other):
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.objects.active = body
    mod = body.modifiers.new(name="BoolUnion", type='BOOLEAN')
    mod.operation = 'UNION'
    mod.object = other
    mod.solver = 'EXACT'
    bpy.ops.object.modifier_apply(modifier="BoolUnion")
    bpy.data.objects.remove(other, do_unlink=True)
    return body
'''

_MATERIAL_ASSIGN = '''\
import bpy
def assign_material(obj, color=(0.8, 0.8, 0.8, 1.0), name="PrintMaterial"):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = False
    mat.diffuse_color = color
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return mat
'''

_CURVE_BEZIER = '''\
import bpy
def create_bezier_curve(points, depth=0.002, resolution=12):
    bpy.ops.curve.primitive_bezier_curve_add()
    obj = bpy.context.active_object
    obj.data.dimensions = '3D'
    obj.data.fill_mode = 'FULL'
    obj.data.extrude = depth
    obj.data.resolution_u = resolution
    spline = obj.data.splines[0]
    spline.bezier_points.add(len(points) - 1)
    for i, (co, handle_left, handle_right) in enumerate(points):
        bp = spline.bezier_points[i]
        bp.co = co
        bp.handle_left = handle_left
        bp.handle_right = handle_right
    bpy.ops.object.convert(target='MESH')
    return bpy.context.active_object
'''

_UV_UNWRAP = '''\
import bpy
def smart_uv_unwrap(obj, angle_limit=66, island_margin=0.001):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=angle_limit, island_margin=island_margin)
    bpy.ops.object.mode_set(mode='OBJECT')
'''

_DISPLACE_MODIFIER = '''\
import bpy
def displace_modifier(obj, strength=0.005, mid_level=0.5, texture=None):
    mod = obj.modifiers.new(name="Displace", type='DISPLACE')
    mod.strength = strength
    mod.mid_level = mid_level
    if texture:
        mod.texture = texture
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Displace")
    return obj
'''

_SOLIDIFY_BOOLEAN_SEQUENCE = '''\
import bpy
def solidify_then_boolean(body, cutter, thickness=0.002):
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.select_all(action='DESELECT')
    body.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    solid = body.modifiers.new(name="Solidify", type='SOLIDIFY')
    solid.thickness = thickness
    solid.offset = -1.0
    solid.use_even_offset = True
    solid.fill_rim = True
    bpy.ops.object.modifier_apply(modifier="Solidify")
    for obj in (body, cutter):
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.objects.active = body
    mod = body.modifiers.new(name="Bool", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    mod.solver = 'EXACT'
    bpy.ops.object.modifier_apply(modifier="Bool")
    bpy.data.objects.remove(cutter, do_unlink=True)
    return body
'''


CORPUS: list[dict] = [
    {"id": "reset_scene", "collection": "official_docs",
     "description": "Cancella tutti gli oggetti mesh della scena.",
     "tags": ["reset", "delete", "scene", "clean"], "code": _RESET_SCENE},
    {"id": "boolean_difference_safe", "collection": "official_docs",
     "description": "Boolean DIFFERENCE con transform_apply e solver EXACT.",
     "tags": ["boolean", "difference", "cutter", "exact"], "code": _BOOLEAN_DIFFERENCE},
    {"id": "primitive_cylinder", "collection": "official_docs",
     "description": "Crea cilindro parametrizzato con N lati configurabile.",
     "tags": ["cylinder", "primitive", "mesh", "radius"], "code": _CYLINDER},
    {"id": "solidify_shell", "collection": "official_docs",
     "description": "Solidify per spessore pareti, offset -1 per stampa 3D.",
     "tags": ["solidify", "thickness", "shell", "wall", "3d print"], "code": _SOLIDIFY},
    {"id": "weld_and_fix_normals", "collection": "official_docs",
     "description": "Weld + normals_make_consistent per mesh manifold.",
     "tags": ["weld", "normals", "manifold", "merge", "repair"], "code": _WELD_NORMALS},
    {"id": "export_stl", "collection": "official_docs",
     "description": "Esporta STL con global_scale=1000 per slicer.",
     "tags": ["export", "stl", "slicer", "scale"], "code": _EXPORT_STL},
    {"id": "remesh_voxel", "collection": "community_code",
     "description": "Remesh voxel per uniformare densita prima di boolean.",
     "tags": ["remesh", "voxel", "cleanup", "density"], "code": _REMESH_VOXEL},
    {"id": "fill_holes_nonmanifold", "collection": "community_code",
     "description": "Chiude buchi in mesh tramite select_non_manifold.",
     "tags": ["fill", "holes", "non-manifold", "watertight"], "code": _FILL_HOLES},
    {"id": "bmesh_merge_vertices", "collection": "community_code",
     "description": "Fonde vertici duplicati via bmesh.remove_doubles.",
     "tags": ["bmesh", "merge", "doubles", "vertices", "weld"], "code": _BMESH_MERGE},
    {"id": "create_text_object", "collection": "community_code",
     "description": "Testo 3D convertito in mesh per incisioni.",
     "tags": ["text", "font", "label", "engrave", "convert"], "code": _TEXT_OBJECT},
    {"id": "copy_object", "collection": "community_code",
     "description": "Duplica oggetto con offset per pattern ripetitivi.",
     "tags": ["duplicate", "copy", "clone", "array"], "code": _DUPLICATE_OBJECT},
    {"id": "diagnose_non_manifold", "collection": "error_patterns",
     "description": "Diagnostica: non-manifold, facce zero, vertici liberi.",
     "tags": ["diagnose", "non-manifold", "repair", "validate"], "code": _DIAGNOSE_MESH},
    {"id": "safe_boolean_with_validation", "collection": "error_patterns",
     "description": "Boolean con validazione type e scale pre/post.",
     "tags": ["boolean", "validation", "safe", "robust"], "code": _SAFE_BOOLEAN},
    {"id": "primitive_sphere", "collection": "official_docs",
     "description": "Crea sfera UV parametrizzata con segmenti configurabili.",
     "tags": ["sphere", "primitive", "mesh", "uv sphere", "segments"], "code": _SPHERE},
    {"id": "primitive_cone", "collection": "official_docs",
     "description": "Crea cono parametrizzato con vertici e profondita configurabili.",
     "tags": ["cone", "primitive", "mesh", "vertices"], "code": _CONE},
    {"id": "primitive_torus", "collection": "official_docs",
     "description": "Crea toro parametrizzato con raggi e segmenti configurabili.",
     "tags": ["torus", "primitive", "mesh", "ring", "donut"], "code": _TORUS},
    {"id": "modifier_array", "collection": "community_code",
     "description": "Array modifier con conteggio e offset per pattern ripetitivi lineari.",
     "tags": ["array", "modifier", "pattern", "repeat", "linear"], "code": _ARRAY_MODIFIER},
    {"id": "modifier_mirror", "collection": "community_code",
     "description": "Mirror modifier con merge automatico per simmetria.",
     "tags": ["mirror", "modifier", "symmetry", "merge", "axis"], "code": _MIRROR_MODIFIER},
    {"id": "modifier_screw", "collection": "community_code",
     "description": "Screw modifier per rivoluzioni e spirali (lathe).",
     "tags": ["screw", "modifier", "revolution", "lathe", "spiral"], "code": _SCREW_MODIFIER},
    {"id": "modifier_bevel", "collection": "community_code",
     "description": "Bevel modifier per smussare spigoli vivi con segmenti controllati.",
     "tags": ["bevel", "modifier", "chamfer", "edge", "smooth"], "code": _BEVEL_MODIFIER},
    {"id": "modifier_decimate", "collection": "community_code",
     "description": "Decimate modifier per ridurre il conteggio poligoni mantenendo la forma.",
     "tags": ["decimate", "modifier", "optimize", "polygons", "simplify"], "code": _DECIMATE_MODIFIER},
    {"id": "boolean_union_safe", "collection": "official_docs",
     "description": "Boolean UNION con transform_apply e solver EXACT per fondere mesh.",
     "tags": ["boolean", "union", "merge", "combine", "exact"], "code": _BOOLEAN_UNION},
    {"id": "assign_material_basic", "collection": "community_code",
     "description": "Assegna materiale base a un oggetto mesh con colore diffuso.",
     "tags": ["material", "color", "assign", "shader", "diffuse"], "code": _MATERIAL_ASSIGN},
    {"id": "bezier_curve_to_mesh", "collection": "community_code",
     "description": "Crea curva Bezier 3D da punti di controllo e converti in mesh.",
     "tags": ["curve", "bezier", "spline", "convert", "extrude"], "code": _CURVE_BEZIER},
    {"id": "uv_smart_project", "collection": "community_code",
     "description": "UV unwrap automatico con smart project per texture mapping.",
     "tags": ["uv", "unwrap", "smart", "texture", "mapping"], "code": _UV_UNWRAP},
    {"id": "modifier_displace", "collection": "community_code",
     "description": "Displace modifier per deformazione basata su texture o noise.",
     "tags": ["displace", "modifier", "deform", "noise", "texture"], "code": _DISPLACE_MODIFIER},
    {"id": "solidify_then_boolean_sequence", "collection": "error_patterns",
     "description": "Applica Solidify PRIMA di Boolean: ordine corretto per mesh manifold.",
     "tags": ["solidify", "boolean", "order", "sequence", "manifold", "wall"], "code": _SOLIDIFY_BOOLEAN_SEQUENCE},
]


class VectorDB:
    PERSIST_DIR = Path.home() / ".bpy_vectordb" / "chroma"
    EMBED_MODEL = "all-MiniLM-L6-v2"

    def __init__(self) -> None:
        self._client = None
        self._embedder = None
        self._collections: dict = {}

    def _ensure_deps(self) -> None:
        if not _DEPS_OK:
            raise ImportError("chromadb e sentence-transformers richiesti. pip install chromadb sentence-transformers")

    def _init_client(self) -> None:
        if self._client is not None:
            return
        self._ensure_deps()
        self.PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.PERSIST_DIR))
        self._embedder = SentenceTransformer(self.EMBED_MODEL)

    async def _run_blocking(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def build(self, force_rebuild: bool = False) -> None:
        self._init_client()

        collection_names: set[str] = {doc["collection"] for doc in CORPUS}

        for coll_name in collection_names:
            if force_rebuild:
                try:
                    self._client.delete_collection(coll_name)
                except Exception:
                    pass
            collection = self._client.get_or_create_collection(
                name=coll_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._collections[coll_name] = collection

        by_coll: dict[str, list] = {n: [] for n in collection_names}
        for doc in CORPUS:
            by_coll[doc["collection"]].append(doc)

        for coll_name, snippets in by_coll.items():
            collection = self._collections[coll_name]
            existing_ids = set(collection.get()["ids"])

            ids, docs, embeddings, metas = [], [], [], []

            for snippet in snippets:
                embed_text = (
                    f"{snippet['description']}\n"
                    f"tags: {', '.join(snippet['tags'])}\n"
                    f"code_preview: {snippet['code'][:300]}"
                )
                snippet_hash = hashlib.md5(snippet["code"].encode()).hexdigest()[:8]
                versioned_id = f"{snippet['id']}_{snippet_hash}"

                if versioned_id in existing_ids:
                    continue

                embedding = await self._run_blocking(self._embedder.encode, embed_text)
                ids.append(versioned_id)
                docs.append(snippet["code"])
                embeddings.append(embedding.tolist())
                metas.append({
                    "description": snippet["description"],
                    "tags": json.dumps(snippet["tags"]),
                    "snippet_id": snippet["id"],
                })

            if ids:
                collection.add(
                    ids=ids,
                    documents=docs,
                    embeddings=embeddings,
                    metadatas=metas,
                )

    async def search(
        self,
        query: str,
        collection: str = "",
        n_results: int = 3,
    ) -> str:
        self._init_client()

        if not collection:
            return await self.search_all(query=query, n_per_collection=n_results)

        if collection not in self._collections:
            try:
                self._collections[collection] = self._client.get_collection(collection)
            except Exception:
                return ""

        coll = self._collections[collection]
        truncated_query = query[:2000]
        query_embedding = await self._run_blocking(self._embedder.encode, truncated_query)

        results = coll.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=min(n_results, coll.count()),
            include=["documents", "metadatas", "distances"],
        )

        if not results["ids"][0]:
            return ""

        output_parts = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            score = 1.0 - dist
            tag_str = ", ".join(json.loads(meta["tags"]))
            output_parts.append(
                f"[{meta['snippet_id'].upper()} | score={score:.3f}]\n"
                f"# {meta['description']}\n"
                f"# tags: {tag_str}\n"
                f"{doc}"
            )

        return "\n\n".join(output_parts)

    async def search_all(self, query: str, n_per_collection: int = 2) -> str:
        all_collections = ["official_docs", "community_code", "error_patterns"]
        parts = []
        for coll in all_collections:
            result = await self.search(query, coll, n_results=n_per_collection)
            if result:
                parts.append(f"# === {coll} ===\n{result}")
        return "\n\n".join(parts)

    async def search_by_error(self, error_text: str, n_results: int = 3) -> str:
        import re as _re
        keywords = set()
        for line in error_text.splitlines():
            if "Error:" in line or "Exception:" in line:
                for word in _re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", line):
                    keywords.add(word)
            if "line " in line and "module" in line.lower():
                for word in _re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", line):
                    keywords.add(word)
        main_errors = [l for l in error_text.splitlines() if "Error:" in l or "Exception:" in l]
        error_types = set()
        for e in main_errors:
            parts = e.split(":")
            if parts:
                error_types.add(parts[0].strip())

        query_parts = list(keywords.union(error_types))
        query = " ".join(query_parts[:15]) if query_parts else error_text[:200]

        if error_types:
            query = " ".join(error_types) + " " + query

        return await self.search(query, "error_patterns", n_results=n_results)

    async def _index_snippets(self, snippets: list[dict]) -> int:
        self._init_client()
        added = 0

        by_collection: dict[str, list[dict]] = {}
        for s in snippets:
            coll_name = s.get("collection", "community_code")
            by_collection.setdefault(coll_name, []).append(s)

        for coll_name, coll_snippets in by_collection.items():
            collection = self._client.get_or_create_collection(
                name=coll_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._collections[coll_name] = collection
            existing_ids = set(collection.get()["ids"])

            ids, docs, embeddings, metas = [], [], [], []

            for s in coll_snippets:
                embed_text = (
                    f"{s['description']}\n"
                    f"tags: {', '.join(s.get('tags', []))}\n"
                    f"code_preview: {s['code'][:300]}"
                )
                snippet_hash = hashlib.md5(s["code"].encode()).hexdigest()[:8]
                versioned_id = f"{s['id']}_{snippet_hash}"

                if versioned_id in existing_ids:
                    continue

                embedding = await self._run_blocking(self._embedder.encode, embed_text)
                ids.append(versioned_id)
                docs.append(s["code"])
                embeddings.append(embedding.tolist())
                metas.append({
                    "description": s["description"],
                    "tags": json.dumps(s.get("tags", [])),
                    "snippet_id": s["id"],
                })

            if ids:
                collection.add(
                    ids=ids,
                    documents=docs,
                    embeddings=embeddings,
                    metadatas=metas,
                )
                added += len(ids)

        return added

    def stats(self) -> dict:
        self._init_client()
        result = {}
        for coll_name in ["official_docs", "community_code", "error_patterns"]:
            try:
                coll = self._client.get_collection(coll_name)
                self._collections[coll_name] = coll
                result[coll_name] = coll.count()
            except Exception:
                result[coll_name] = 0
        return result
