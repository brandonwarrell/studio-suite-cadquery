"""
2D floor plan.

The plan is built ONCE as a DXF document (ezdxf, units = inches, feet-inch dimension text).
floorplan.svg and plan.png are then rendered from that same DXF with ezdxf's drawing add-on, so the
three files can never disagree with each other - or with the 3D model, because everything comes from
layout.derive(CONFIG).
"""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf import units as ezunits
from ezdxf.enums import TextEntityAlignment

if __package__ in (None, ""):                      # allow `python studio_suite/floorplan.py`
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "studio_suite"

from .config import CONFIG                                                  # noqa: E402
from .layout import derive, fmt_ftin, fmt_in, WALL_INFO, KITCHEN_FIXTURES  # noqa: E402

# layer: (ACI colour, lineweight 1/100 mm, linetype)
LAYERS = {
    "WALLS":      (7, 50, "CONTINUOUS"),
    "WALL_FILL":  (8, 0, "CONTINUOUS"),
    "PARTITIONS": (7, 35, "CONTINUOUS"),
    "WINDOWS":    (5, 18, "CONTINUOUS"),
    "DOORS":      (30, 18, "CONTINUOUS"),
    "DOORS_HID":  (30, 13, "DASHED"),
    "FIXTURES":   (4, 18, "CONTINUOUS"),
    "KITCHEN":    (4, 18, "CONTINUOUS"),
    "HEADER":     (1, 35, "DASHED"),
    "REF":        (8, 13, "DASHED"),
    "DIMS":       (3, 13, "CONTINUOUS"),
    "TEXT":       (7, 13, "CONTINUOUS"),
    "ZONES":      (6, 13, "CONTINUOUS"),
    "TITLE":      (7, 13, "CONTINUOUS"),
}
TXT, TXT_S, TXT_L = 4.0, 3.0, 6.0        # text heights, inches
DIM_OVR = {"dimtxt": 4.0, "dimasz": 3.0, "dimtsz": 2.0, "dimexe": 2.0, "dimexo": 1.5, "dimgap": 1.0,
           "dimtad": 1, "dimclrt": 7, "dimclrd": 3, "dimclre": 3}
MC = TextEntityAlignment.MIDDLE_CENTER
ML = TextEntityAlignment.MIDDLE_LEFT


def _split_strip(rect, axis, gaps):
    """Split a wall strip into the solid segments left between the (a0, a1) openings."""
    x0, y0, x1, y1 = rect
    lo, hi = (x0, x1) if axis == "x" else (y0, y1)
    segs, cur = [], lo
    for g0, g1 in sorted(gaps):
        g0, g1 = max(g0, lo), min(g1, hi)
        if g0 > cur + 1e-9:
            segs.append((cur, y0, g0, y1) if axis == "x" else (x0, cur, x1, g0))
        cur = max(cur, g1)
    if cur < hi - 1e-9:
        segs.append((cur, y0, hi, y1) if axis == "x" else (x0, cur, x1, hi))
    return segs


class Plan:
    """Thin ezdxf wrapper so the drawing code below reads like a plan, not like DXF plumbing."""

    def __init__(self, doc):
        self.doc = doc
        self.msp = doc.modelspace()

    def line(self, p, q, layer):
        self.msp.add_line(p, q, dxfattribs={"layer": layer})

    def pline(self, pts, layer, close=True):
        self.msp.add_lwpolyline(pts, close=close, dxfattribs={"layer": layer})

    def rect(self, r, layer):
        x0, y0, x1, y1 = r
        self.pline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer)

    def fill(self, r, layer, color):
        x0, y0, x1, y1 = r
        h = self.msp.add_hatch(color=color, dxfattribs={"layer": layer})
        h.paths.add_polyline_path([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], is_closed=True)

    def circle(self, c, r, layer):
        self.msp.add_circle(c, r, dxfattribs={"layer": layer})

    def arc(self, c, r, a0, a1, layer):
        self.msp.add_arc(c, r, a0, a1, dxfattribs={"layer": layer})

    def text(self, s, p, h=TXT, layer="TEXT", rot=0.0, align=MC):
        self.msp.add_text(s, height=h, rotation=rot, dxfattribs={"layer": layer}).set_placement(p, align=align)

    def dim_h(self, x0, x1, y_pts, y_line, label):
        d = self.msp.add_linear_dim(base=(x0, y_line), p1=(x0, y_pts), p2=(x1, y_pts), angle=0, text=label,
                                    override=DIM_OVR, dxfattribs={"layer": "DIMS"})
        d.render()

    def dim_v(self, y0, y1, x_pts, x_line, label):
        d = self.msp.add_linear_dim(base=(x_line, y0), p1=(x_pts, y0), p2=(x_pts, y1), angle=90, text=label,
                                    override=DIM_OVR, dxfattribs={"layer": "DIMS"})
        d.render()


def build_dxf(cfg: dict = CONFIG):
    """Return (ezdxf document, layout dict)."""
    L = derive(cfg)
    errors = [m for lvl, m in L["checks"] if lvl == "ERROR"]
    if errors:
        raise ValueError("layout errors:\n  " + "\n  ".join(errors))

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezunits.IN
    doc.header["$LUNITS"] = 4          # architectural
    doc.header["$LTSCALE"] = 8.0
    doc.header["$MEASUREMENT"] = 0     # English
    for name, (color, lw, lt) in LAYERS.items():
        doc.layers.add(name, color=color, lineweight=lw, linetype=lt)
    P = Plan(doc)
    X, Y, ext, it = L["X"], L["Y"], L["ext"], L["int"]

    # ---------------------------------------------------------------- walls (solid segments between openings)
    for w in L["walls"].values():
        axis = WALL_INFO[w["side"]]["axis"]
        for seg in _split_strip(w["rect"], axis, [(op["a0"], op["a1"]) for op in w["openings"]]):
            P.rect(seg, "WALLS")
            P.fill(seg, "WALL_FILL", 8)
    for p in L["partitions"].values():
        if p["z"][0] > 1e-6:           # header over a door opening: not a wall in plan
            continue
        rect = p["rect"]
        axis = "x" if (rect[2] - rect[0]) >= (rect[3] - rect[1]) else "y"
        for seg in _split_strip(rect, axis, [(op["a0"], op["a1"]) for op in p["openings"]]):
            P.rect(seg, "PARTITIONS")
            P.fill(seg, "WALL_FILL", 9)

    # ---------------------------------------------------------------- windows
    for wid, op in L["windows"].items():
        a0, a1, fi, fo = op["a0"], op["a1"], op["face_in"], op["face_out"]
        mid = (fi + fo) / 2

        def pt(a, t, axis=op["axis"]):
            return (a, t) if axis == "x" else (t, a)

        for t in (fi, fo, mid):
            P.line(pt(a0, t), pt(a1, t), "WINDOWS")
        P.line(pt(a0, fi), pt(a0, fo), "WINDOWS")
        P.line(pt(a1, fi), pt(a1, fo), "WINDOWS")
        nx, ny = op["inward"]
        c = pt((a0 + a1) / 2, fo)
        P.text(wid, (c[0] - nx * 9, c[1] - ny * 9), TXT_S, rot=0 if op["axis"] == "x" else 90)

    # ---------------------------------------------------------------- exterior doors
    for did, op in L["doors"].items():
        a0, a1, fi, fo = op["a0"], op["a1"], op["face_in"], op["face_out"]

        def pt(a, t, axis=op["axis"]):
            return (a, t) if axis == "x" else (t, a)

        P.line(pt(a0, fi), pt(a0, fo), "DOORS")
        P.line(pt(a1, fi), pt(a1, fo), "DOORS")
        P.line(pt(a0, fo), pt(a1, fo), "DOORS")                       # threshold
        P.line(op["hinge_pt"], op["leaf_end"], "DOORS")
        arc = op["arc"]
        P.arc(arc["center"], arc["r"], arc["start"], arc["end"], "DOORS")
        hx, hy = op["hinge_pt"]
        ex, ey = op["leaf_end"]
        sx, sy = op["strike_pt"]
        mx, my = (hx + ex) / 2, (hy + ey) / 2
        dx, dy = (sx - hx), (sy - hy)
        n = math.hypot(dx, dy) or 1.0
        mx, my = mx + dx / n * 5.0, my + dy / n * 5.0
        P.text(f"{did} {fmt_ftin(op['width'])} x {fmt_ftin(op['z1'])}", (mx, my), TXT_S,
               rot=0 if op["axis"] == "y" else 90)

    # ---------------------------------------------------------------- pocket door
    pdo = L["pocket_door"]
    wy0, wy1 = pdo["wall_rect"][1], pdo["wall_rect"][3]
    P.line((pdo["a0"], wy0), (pdo["a0"], wy1), "DOORS")
    P.line((pdo["a1"], wy0), (pdo["a1"], wy1), "DOORS")
    P.rect(pdo["pocket_rect"], "DOORS_HID")
    P.rect(pdo["panel_rect"], "DOORS")
    P.line((pdo["a0"], (wy0 + wy1) / 2), (pdo["a1"], (wy0 + wy1) / 2), "DOORS_HID")   # slide path
    P.text(f"POCKET DR {fmt_ftin(pdo['width'])}", ((pdo["a0"] + pdo["a1"]) / 2, wy0 - 6), TXT_S)

    # ---------------------------------------------------------------- closet bifold (two folded pairs, room side)
    bf = L["closet_bifold"]
    x0, x1, yf = bf["x0"], bf["x1"], bf["y_face"]
    panel = (x1 - x0) / 4.0
    ca, sa = math.cos(math.radians(30)), math.sin(math.radians(30))
    P.pline([(x0, yf), (x0 + panel * ca, yf + panel * sa), (x0 + 2 * panel * ca, yf)], "DOORS", close=False)
    P.pline([(x1, yf), (x1 - panel * ca, yf + panel * sa), (x1 - 2 * panel * ca, yf)], "DOORS", close=False)
    P.line((x0, yf), (x1, yf), "DOORS_HID")
    P.text(bf["label"], ((x0 + x1) / 2, yf + panel * sa + 5), TXT_S)

    # ---------------------------------------------------------------- fixtures
    for name, f in L["fixtures"].items():
        bx0, by0, _, bx1, by1, _ = f["box"]
        layer = "KITCHEN" if name in KITCHEN_FIXTURES else "FIXTURES"
        rect = (bx0, by0, bx1, by1)
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        P.rect(rect, layer)
        if name == "fx_shower":
            P.circle((cx, cy), 2.0, layer)
            P.text("SHOWER", (cx, cy + 7), TXT, layer="TEXT")
            P.text(f"{fmt_ftin(bx1 - bx0)} x {fmt_ftin(by1 - by0)}", (cx, cy - 6), TXT_S)
        elif name == "fx_toilet":
            tank_w = min(7.0, (bx1 - bx0) * 0.3)
            P.rect((bx0, by0 + 1.5, bx0 + tank_w, by1 - 1.5), layer)
            r = max(1.0, min((by1 - by0) / 2 - 1.5, (bx1 - bx0 - tank_w) / 2 - 1.0))
            P.circle((bx0 + tank_w + (bx1 - bx0 - tank_w) / 2, cy), r, layer)
            P.text("WC", (bx1 + 6, cy), TXT_S, align=ML)
        elif name == "fx_vanity":
            P.circle((cx, cy + 2), 7.0, layer)
            P.text("VANITY", (cx, by0 + 3.5), TXT_S)
        elif name == "fx_counter":
            P.text(f["label"], (cx, by0 - 6), TXT_S)
        elif name == "fx_sink":
            P.rect((bx0 + 2, by0 + 2, bx1 - 2, by1 - 2), layer)
            P.text("SINK", (cx, cy), TXT_S)
        elif name == "fx_fridge":
            P.line((bx0, by0), (bx1, by1), layer)
            P.text("REF", (cx, cy + 4), TXT_S)
            P.text("U/C", (cx, cy - 4), TXT_S)

    # ---------------------------------------------------------------- header line + AC unit
    (h0, h1) = L["header"]["line"]
    P.line(h0, h1, "HEADER")
    hx = (h0[0] + h1[0]) / 2
    P.text(f"HEADER {L['header']['size']}", (hx, h0[1] + 9), TXT_S, layer="HEADER")
    P.text("VERIFY LOAD-BEARING", (hx, h0[1] + 4.5), TXT_S, layer="HEADER")
    ax0, ay0, _, ax1, ay1, _ = L["ac_unit"]["box"]
    P.rect((ax0, ay0, ax1, ay1), "REF")
    P.text("AC UNIT", ((ax0 + ax1) / 2, (ay0 + ay1) / 2 + 3), TXT_S, layer="REF")
    P.text("(REF)", ((ax0 + ax1) / 2, (ay0 + ay1) / 2 - 3), TXT_S, layer="REF")

    # ---------------------------------------------------------------- zone labels
    bath = L["bath"]
    P.text("BATH", (bath["x1"] - 18, bath["y0"] + 62), TXT, layer="ZONES")
    P.text(f"{L['zones']['Bathroom']['sqft']:.0f} SF", (bath["x1"] - 18, bath["y0"] + 55), TXT_S, layer="ZONES")
    kz = L["kitchenette"]["zone_rect"]
    P.text("KITCHENETTE", ((kz[0] + kz[2]) / 2, kz[1] + 34), TXT, layer="ZONES")
    P.text(f"{L['zones']['Kitchenette (open, under header)']['sqft']:.0f} SF", ((kz[0] + kz[2]) / 2, kz[1] + 27),
           TXT_S, layer="ZONES")
    P.text("LIVING / SLEEPING", (X / 2, Y * 0.30), TXT, layer="ZONES")
    P.text(f"{L['zones']['Living / sleeping (net of closet)']['sqft']:.0f} SF net", (X / 2, Y * 0.30 - 7), TXT_S,
           layer="ZONES")
    cl = L["closet"]
    ccx, ccy = (cl["x0"] + cl["x1"]) / 2, (cl["y0"] + cl["y1"] - it) / 2
    P.text("CLOSET", (ccx, ccy + 2.5), TXT_S, layer="ZONES")
    P.text(f"{L['zones']['Closet']['sqft']:.0f} SF", (ccx, ccy - 3.0), TXT_S, layer="ZONES")

    # ---------------------------------------------------------------- dimensions
    row1, row2 = 20.0, 44.0
    row_bottom = max(row2, -ay0 - ext + 12.0)          # overall dim clears the AC reference box below the wall
    P.dim_h(cl["x0"], X, -ext, -ext - row1, fmt_ftin(cl["x1"] - cl["x0"]))               # closet width
    y_pts = (ay0 - 2.0) if (ax0 < 0.0 < ax1 or ax0 < X < ax1) else -ext                  # start below the AC box if it straddles a corner
    P.dim_h(0.0, X, y_pts, -ext - row_bottom, fmt_ftin(X))                               # 15'-0" interior
    P.dim_v(0.0, cl["y1"], X + ext, X + ext + row1, fmt_ftin(cl["y1"]))                  # closet depth
    P.dim_v(0.0, Y, X + ext, X + ext + row2, fmt_ftin(Y))                                # 21'-0" interior
    P.dim_v(bath["y0"], bath["y1"], -ext, -ext - row1 - 4, fmt_ftin(bath["y1"] - bath["y0"]))   # 7'-0" bath depth
    P.dim_h(bath["x0"], bath["x1"], Y + ext, Y + ext + row2,                             # 8'-0" bath width, 2nd row
            fmt_ftin(bath["x1"] - bath["x0"]) + " (BATH)")                               # above the window dims
    for wid, op in L["windows"].items():                                                 # window widths
        if op["axis"] == "x":
            P.dim_h(op["a0"], op["a1"], op["face_out"], op["face_out"] + row1 * (1 if op["wall"] == "top" else -1),
                    fmt_ftin(op["width"]))
        else:
            P.dim_v(op["a0"], op["a1"], op["face_out"], op["face_out"] + row1 * (1 if op["wall"] == "right" else -1),
                    fmt_ftin(op["width"]))

    # ---------------------------------------------------------------- title block
    ty = -ext - row_bottom - 22
    P.text(f"STUDIO SUITE  -  {fmt_ftin(X)} x {fmt_ftin(Y)} ADDITION  -  FLOOR PLAN", (-ext, ty), TXT_L,
           layer="TITLE", align=ML)
    notes = [
        f"Units: inches (feet-inch labels).  Origin = bottom-left interior corner.  Ceiling {fmt_ftin(L['H'])} (assumed).",
        f"Walls: exterior {fmt_in(ext)} / partitions {fmt_in(it)} (assumed).  "
        f"Windows: sill {fmt_ftin(next(iter(L['windows'].values()))['z0']) if L['windows'] else '-'}, "
        f"head {fmt_ftin(next(iter(L['windows'].values()))['z1']) if L['windows'] else '-'}.",
        f"Header at y={fmt_ftin(L['resolved']['header.y'] * 12)} x={fmt_ftin(L['resolved']['header.x0'] * 12)}.."
        f"{fmt_ftin(L['resolved']['header.x1'] * 12)}: {L['header']['size']} placeholder - VERIFY LOAD-BEARING.",
        "Generated by studio_suite/floorplan.py from CONFIG (config.py).  Massing only - not for construction.",
    ]
    for i, line in enumerate(notes):
        P.text(line, (-ext, ty - 9 - i * 6), TXT_S, layer="TITLE", align=ML)
    return doc, L


def render_svg(doc, path):
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, config
    from ezdxf.addons.drawing.svg import SVGBackend

    ctx = RenderContext(doc)
    backend = SVGBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
    Frontend(ctx, backend, config=cfg).draw_layout(doc.modelspace(), finalize=True)
    page = layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(10))
    Path(path).write_text(backend.get_string(page), encoding="utf-8")
    return Path(path)


def render_png(doc, path, dpi=150):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext, config
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    from ezdxf import bbox

    msp = doc.modelspace()
    ext = bbox.extents(msp, fast=True)
    (x0, y0, _), (x1, y1, _) = ext.extmin, ext.extmax
    pad = 0.02 * max(x1 - x0, y1 - y0)
    w_in, h_in = (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad
    fig_w = 11.0
    fig = plt.figure(figsize=(fig_w, fig_w * h_in / w_in))
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
    Frontend(ctx, MatplotlibBackend(ax, adjust_figure=False), config=cfg).draw_layout(msp, finalize=True)
    ax.set_xlim(x0 - pad, x1 + pad)
    ax.set_ylim(y0 - pad, y1 + pad)
    ax.set_aspect("equal")
    ax.set_axis_off()
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return Path(path)


def export_floorplan(cfg: dict = CONFIG, out_dir="output"):
    """Write floorplan.dxf, floorplan.svg and plan.png into out_dir.  Returns the three paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doc, _ = build_dxf(cfg)
    dxf = out / "floorplan.dxf"
    doc.saveas(dxf)
    svg = render_svg(doc, out / "floorplan.svg")
    png = render_png(doc, out / "plan.png")
    return [dxf, svg, png]


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    for p in export_floorplan(CONFIG, root / "output"):
        print("wrote", p)
