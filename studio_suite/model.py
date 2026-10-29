"""
3D build with CadQuery.  All geometry is in INCHES (derived by layout.py); exporters convert as needed.

build_model(CONFIG) -> (parts, L)   parts = list[Part], one named solid per element
export_step / export_stl / export_glb / render_iso
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import cadquery as cq

from .config import CONFIG
from .layout import derive, KITCHEN_FIXTURES

UNIT_SCALE = {"in": 1.0, "mm": 25.4, "cm": 2.54, "m": 0.0254}   # inches -> unit

COLORS = {   # rgb 0..1
    "floor":    (0.80, 0.78, 0.74),
    "ceiling":  (0.96, 0.96, 0.95),
    "wall_ext": (0.87, 0.87, 0.85),
    "wall_int": (0.94, 0.91, 0.82),
    "door":     (0.62, 0.44, 0.27),
    "header":   (0.78, 0.30, 0.20),
    "fixture":  (0.55, 0.72, 0.88),
    "kitchen":  (0.76, 0.58, 0.42),
    "ref":      (0.55, 0.55, 0.55),
    "glass":    (0.60, 0.82, 0.95),
}

# outward normals of the exterior walls, used to fade the camera-facing walls in iso.png
OUTWARD = {"wall_ext_bottom": (0, -1, 0), "wall_ext_top": (0, 1, 0),
           "wall_ext_left": (-1, 0, 0), "wall_ext_right": (1, 0, 0)}

SHORT_LABEL = {
    "fx_shower": "shower", "fx_toilet": "toilet", "fx_vanity": "vanity", "fx_counter": "counter",
    "fx_sink": "sink", "fx_fridge": "fridge", "header_beam": "header (verify)", "ref_ac_unit": "AC (ref)",
    "closet_bifold_door": "closet bifold",
}


@dataclass
class Part:
    name: str
    shape: cq.Shape
    category: str
    label: str = ""
    color: tuple = (0.7, 0.7, 0.7)

    @property
    def bbox(self):
        bb = self.shape.BoundingBox()
        return (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)


def _box(b):
    x0, y0, z0, x1, y1, z1 = b
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, cq.Vector(x0, y0, z0))


def _rect_box(rect, z):
    x0, y0, x1, y1 = rect
    return _box((x0, y0, z[0], x1, y1, z[1]))


def _cut_all(shape, boxes):
    for b in boxes:
        shape = shape.cut(_box(b))
    return shape


def build_model(cfg: dict = CONFIG):
    """Return (parts, layout).  Raises ValueError if layout.derive reports an ERROR."""
    L = derive(cfg)
    errors = [m for lvl, m in L["checks"] if lvl == "ERROR"]
    if errors:
        raise ValueError("layout errors:\n  " + "\n  ".join(errors))

    parts: list[Part] = []
    parts.append(Part("floor_slab", _box(L["floor"]["box"]), "floor", "floor slab", COLORS["floor"]))
    if L["ceiling"]["include"]:
        parts.append(Part("ceiling", _box(L["ceiling"]["box"]), "ceiling", "ceiling plate", COLORS["ceiling"]))

    for name, w in L["walls"].items():
        s = _rect_box(w["rect"], w["z"])
        s = _cut_all(s, [(op["cut_rect"][0], op["cut_rect"][1], op["z0"], op["cut_rect"][2], op["cut_rect"][3], op["z1"])
                         for op in w["openings"]])
        parts.append(Part(name, s, "wall_ext", w["label"], COLORS["wall_ext"]))

    for name, p in L["partitions"].items():
        s = _rect_box(p["rect"], p["z"])
        s = _cut_all(s, [(op["cut_rect"][0], op["cut_rect"][1], op["z0"], op["cut_rect"][2], op["cut_rect"][3], op["z1"])
                         for op in p["openings"]])
        parts.append(Part(name, s, "wall_int", p["label"], COLORS["wall_int"]))

    bf = L["closet_bifold"]
    parts.append(Part("closet_bifold_door", _rect_box(bf["rect"], bf["z"]), "door", bf["label"], COLORS["door"]))
    parts.append(Part("header_beam", _box(L["header"]["box"]), "header", L["header"]["label"], COLORS["header"]))

    for name, f in L["fixtures"].items():
        cat = "kitchen" if name in KITCHEN_FIXTURES else "fixture"
        s = _cut_all(_box(f["box"]), f.get("cuts", []))
        parts.append(Part(name, s, cat, f["label"], COLORS[cat]))

    parts.append(Part("ref_ac_unit", _box(L["ac_unit"]["box"]), "ref", L["ac_unit"]["label"], COLORS["ref"]))

    exp = cfg.get("export", {})
    if exp.get("include_window_glass"):
        for wid, op in L["windows"].items():
            parts.append(Part(f"glass_{wid}", _box(op["panel_box"]), "glass", f"{wid} glazing", COLORS["glass"]))
    if exp.get("include_door_leaves"):
        for did, op in L["doors"].items():
            parts.append(Part(f"leaf_{did}", _box(op["panel_box"]), "door", f"{did} leaf", COLORS["door"]))

    # every part must be exactly one valid solid, and names must be unique (they become object names)
    seen = set()
    for p in parts:
        if p.name in seen:
            raise ValueError(f"duplicate part name {p.name}")
        seen.add(p.name)
        solids = p.shape.Solids()
        if len(solids) != 1:
            raise ValueError(f"{p.name}: expected exactly 1 solid, got {len(solids)}")
        if not p.shape.isValid():
            raise ValueError(f"{p.name}: invalid solid")
    return parts, L


# ----------------------------------------------------------------------------- exports
def export_step(parts, path, unit="INCH"):
    """STEP assembly with one named product per part (names survive into FreeCAD / Fusion / SolidWorks)."""
    assy = cq.Assembly(name="studio_suite")
    for p in parts:
        assy.add(p.shape, name=p.name, color=cq.Color(*p.color, 1.0))
    assy.export(str(path), exportType="STEP", unit=unit)
    return Path(path)


def export_stl(parts, path, units="in", tolerance=0.02):
    comp = cq.Compound.makeCompound([p.shape for p in parts])
    scale = UNIT_SCALE[units]
    if abs(scale - 1.0) > 1e-12:
        comp = comp.scale(scale)
    cq.exporters.export(comp, str(path), exportType="STL", tolerance=tolerance, angularTolerance=0.1)
    return Path(path)


def tessellate_parts(parts, tolerance=0.02, angular=0.1):
    """name -> (vertices[N,3] inches, faces[M,3])"""
    meshes = {}
    for p in parts:
        verts, tris = p.shape.tessellate(tolerance, angular)
        V = np.array([[v.x, v.y, v.z] for v in verts], dtype=float)
        F = np.array(tris, dtype=np.int64).reshape(-1, 3)
        meshes[p.name] = (V, F)
    return meshes


def export_glb(parts, meshes, path, units="m", up="Y"):
    """One named mesh + node per part.

    units: "m" | "cm" | "mm" | "in"    up: "Y" (glTF standard; UE5 converts to Z-up) or "Z" (raw, no axis change)
    """
    import trimesh

    scale = UNIT_SCALE[units]
    up = up.upper()
    if up not in ("Y", "Z"):
        raise ValueError("up must be 'Y' or 'Z'")
    scene = trimesh.Scene()
    for p in parts:
        V, F = meshes[p.name]
        V = V * scale
        if up == "Y":                                   # Z-up -> Y-up: (x, y, z) -> (x, z, -y)
            V = np.column_stack([V[:, 0], V[:, 2], -V[:, 1]])
        m = trimesh.Trimesh(vertices=V, faces=F, process=True)
        if m.is_watertight:
            m.fix_normals()
        rgba = [int(round(c * 255)) for c in p.color] + [255]
        try:
            mat = trimesh.visual.material.PBRMaterial(name=f"M_{p.category}", baseColorFactor=rgba,
                                                      metallicFactor=0.0, roughnessFactor=0.85)
            m.visual = trimesh.visual.TextureVisuals(material=mat)
        except Exception:                               # pragma: no cover - fall back to vertex colours
            m.visual.face_colors = rgba
        m.metadata.update({"name": p.name, "category": p.category, "label": p.label})
        scene.add_geometry(m, node_name=p.name, geom_name=p.name)
    scene.metadata.update({
        "units": units, "up_axis": up, "generator": "studio_suite/model.py",
        "note": ("glTF standard: meters, +Y up. UE5 imports as Z-up centimeters." if (units == "m" and up == "Y")
                 else f"raw coordinates: {units}, +{up} up (NOT glTF-standard)"),
    })
    scene.export(str(path))
    return Path(path)


def render_iso(parts, meshes, path, L=None, elev=32.0, azim=-50.0, max_edge=18.0):
    """Quick isometric-ish render with matplotlib.  Camera-facing exterior walls are faded so the interior shows."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection, Line3DCollection
    import trimesh

    ce, se = math.cos(math.radians(elev)), math.sin(math.radians(elev))
    cam = (ce * math.cos(math.radians(azim)), ce * math.sin(math.radians(azim)), se)

    polys, colors, edges = [], [], []
    allv = []
    for p in parts:
        V, F = meshes[p.name]
        allv.append(V)
        for e in p.shape.Edges():                       # real solid edges (all straight lines here)
            a, b = e.startPoint(), e.endPoint()
            edges.append([(a.x, a.y, a.z), (b.x, b.y, b.z)])
        # split big wall/floor triangles so the painter's-algorithm depth sort behaves
        V2, F2 = trimesh.remesh.subdivide_to_size(V, F, max_edge=max_edge)
        alpha = 1.0
        if p.category == "wall_ext":
            n = OUTWARD.get(p.name, (0, 0, 0))
            alpha = 0.15 if (n[0] * cam[0] + n[1] * cam[1] + n[2] * cam[2]) > 0 else 0.95
        elif p.category == "wall_int":
            alpha = 0.55
        elif p.category == "glass":
            alpha = 0.35
        elif p.category == "ceiling":
            alpha = 0.10
        polys.append(V2[F2])
        colors.extend([(*p.color, alpha)] * len(F2))
    P = np.concatenate(polys)
    A = np.vstack(allv)
    lo, hi = A.min(axis=0), A.max(axis=0)

    fig = plt.figure(figsize=(13, 9.5))
    ax = fig.add_subplot(111, projection="3d")
    coll = Poly3DCollection(P, facecolors=colors, edgecolors="none")
    coll.set_zsort("average")
    ax.add_collection3d(coll)
    ax.add_collection3d(Line3DCollection(edges, colors=(0, 0, 0, 0.45), linewidths=0.5))
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(tuple(hi - lo))
    ax.view_init(elev=elev, azim=azim)
    ax.set_proj_type("ortho")
    ax.set_axis_off()

    for p in parts:
        if p.name in SHORT_LABEL:
            x0, y0, _, x1, y1, z1 = p.bbox
            ax.text((x0 + x1) / 2, (y0 + y1) / 2, z1 + 3, SHORT_LABEL[p.name], fontsize=7, ha="center",
                    color="#222222", zorder=1000)
    if L is not None:
        for oid, op in list(L["windows"].items()) + list(L["doors"].items()):
            a = (op["a0"] + op["a1"]) / 2
            x, y = (a, op["face_out"]) if op["axis"] == "x" else (op["face_out"], a)
            ax.text(x, y, op["z1"] + 4, oid, fontsize=8, ha="center", color="#0044aa", weight="bold", zorder=1000)

    ax.set_title("Studio suite - massing model (feet-inch CONFIG, origin = bottom-left interior corner)", fontsize=10)
    legend = [Patch(facecolor=COLORS[c], edgecolor="k", label=c) for c in
              ("wall_ext", "wall_int", "fixture", "kitchen", "door", "header", "ref")]
    ax.legend(handles=legend, loc="lower left", fontsize=7, frameon=False)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return Path(path)
