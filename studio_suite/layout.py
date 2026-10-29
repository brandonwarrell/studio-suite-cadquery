"""
Derived geometry in INCHES, shared by model.py (3D) and floorplan.py (2D), plus validation checks.

Pure Python - no CadQuery import - so the 2D plan can be built without OCCT and so the 3D model and
the floor plan can never disagree about where anything is.

derive(CONFIG) returns a dict L with (all inches):
  L["walls"]       exterior walls: rect (x0,y0,x1,y1), z (z0,z1), openings[]
  L["partitions"]  interior walls (same shape)
  L["fixtures"]    boxes (x0,y0,z0,x1,y1,z1) + optional cut boxes
  L["windows"], L["doors"], L["pocket_door"], L["closet_bifold"], L["header"], L["ac_unit"], L["floor"]
  L["zones"]       plan rectangles + square footage
  L["checks"]      list of ("ERROR"|"WARN", message)
"""
from __future__ import annotations

import math
from fractions import Fraction

from .config import CONFIG, ASSUMPTIONS

IN_PER_FT = 12.0
MIN_JAMB = 4.5            # inches of wall wanted between an opening and a corner / another opening
TOILET_SIDE_CLR = 15.0    # inches, toilet centreline to any side obstruction (IRC R307.1)
TOILET_FRONT_CLR = 21.0   # inches, clear in front of the bowl

# along-wall axis and inward (into the room) unit normal for each exterior wall
WALL_INFO = {
    "bottom": {"axis": "x", "inward": (0.0, 1.0)},
    "top":    {"axis": "x", "inward": (0.0, -1.0)},
    "left":   {"axis": "y", "inward": (1.0, 0.0)},
    "right":  {"axis": "y", "inward": (-1.0, 0.0)},
}
KITCHEN_FIXTURES = ("fx_counter", "fx_sink", "fx_fridge")


def ft(v: float) -> float:
    """feet -> inches"""
    return float(v) * IN_PER_FT


def _is_auto(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() == "auto")


def _res(v, default_ft: float) -> float:
    """Resolve an 'auto' CONFIG value (feet) against the value it follows."""
    return float(default_ft) if _is_auto(v) else float(v)


def fmt_ftin(inches: float, denom: int = 8) -> str:
    """180 -> 15'-0"    30.5 -> 2'-6 1/2"    4.5 -> 0'-4 1/2" """
    neg = inches < 0
    total = Fraction(abs(inches)).limit_denominator(denom)
    whole_ft, rem = divmod(total, 12)
    whole_in = int(rem)
    frac = rem - whole_in
    s = f"{int(whole_ft)}'-{whole_in}"
    if frac:
        s += f" {frac.numerator}/{frac.denominator}"
    return ("-" if neg else "") + s + '"'


def fmt_in(inches: float) -> str:
    """3.5 -> 3.5\" """
    return f"{inches:g}\""


def _overlap(a, b):
    """plan-rect overlap test, rects as (x0,y0,x1,y1); touching edges do not count."""
    eps = 1e-6
    return a[0] < b[2] - eps and a[2] > b[0] + eps and a[1] < b[3] - eps and a[3] > b[1] + eps


def _inside(inner, outer):
    eps = 1e-6
    return (inner[0] >= outer[0] - eps and inner[1] >= outer[1] - eps
            and inner[2] <= outer[2] + eps and inner[3] <= outer[3] + eps)


def _area_sqft(rect):
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1]) / 144.0


def _door_swing(op: dict) -> dict:
    """2D door symbol for an exterior door: hinge point, open-leaf end, strike point, quarter-circle arc."""
    w = op["width"]
    nx, ny = op["inward"]
    face = op["face_in"]
    if op["swing"] == "out":
        nx, ny, face = -nx, -ny, op["face_out"]
    a_h = op["a1"] if op["hinge"] == "high" else op["a0"]
    a_s = op["a0"] if op["hinge"] == "high" else op["a1"]
    if op["axis"] == "x":
        hinge, strike = (a_h, face), (a_s, face)
    else:
        hinge, strike = (face, a_h), (face, a_s)
    leaf_end = (hinge[0] + nx * w, hinge[1] + ny * w)
    ang_e = math.degrees(math.atan2(leaf_end[1] - hinge[1], leaf_end[0] - hinge[0]))
    ang_s = math.degrees(math.atan2(strike[1] - hinge[1], strike[0] - hinge[0]))
    cross = (leaf_end[0] - hinge[0]) * (strike[1] - hinge[1]) - (leaf_end[1] - hinge[1]) * (strike[0] - hinge[0])
    start, end = (ang_e, ang_s) if cross > 0 else (ang_s, ang_e)
    return {"hinge_pt": hinge, "leaf_end": leaf_end, "strike_pt": strike,
            "arc": {"center": hinge, "r": w, "start": start % 360.0, "end": end % 360.0}}


def derive(cfg: dict = CONFIG) -> dict:
    r, wcfg = cfg["room"], cfg["walls"]
    X, Y, H = ft(r["interior_x"]), ft(r["interior_y"]), ft(r["ceiling_h"])
    ext, it = ft(wcfg["exterior_thk"]), ft(wcfg["interior_thk"])

    L = {"X": X, "Y": Y, "H": H, "ext": ext, "int": it,
         "walls": {}, "partitions": {}, "fixtures": {}, "windows": {}, "doors": {},
         "zones": {}, "checks": [], "resolved": {},
         "zone_sum": {"ok": False, "bath_depth_ft": 0.0, "living_depth_ft": 0.0, "sum_ft": 0.0,
                      "interior_y_ft": float(r["interior_y"]), "diff_ft": float("nan"),
                      "tol_ft": float(wcfg["interior_thk"])},
         "areas": {"interior_gross": 0.0, "zones_total": 0.0, "partitions": 0.0, "counter_footprint": 0.0}}
    checks = L["checks"]

    def err(msg):
        checks.append(("ERROR", msg))

    def warn(msg):
        checks.append(("WARN", msg))

    if X <= 0 or Y <= 0 or H <= 0 or ext <= 0 or it <= 0:
        err("room dimensions and wall thicknesses must all be positive")
        return L

    # ------------------------------------------------------------------ exterior walls
    rects = {
        "bottom": (-ext, -ext, X + ext, 0.0),
        "top":    (-ext, Y, X + ext, Y + ext),
        "left":   (-ext, 0.0, 0.0, Y),
        "right":  (X, 0.0, X + ext, Y),
    }
    faces_in = {"bottom": 0.0, "top": Y, "left": 0.0, "right": X}
    faces_out = {"bottom": -ext, "top": Y + ext, "left": -ext, "right": X + ext}
    for side, rect in rects.items():
        L["walls"]["wall_ext_" + side] = {"rect": rect, "z": (0.0, H), "side": side, "openings": [],
                                          "label": f"exterior wall ({side})"}

    def wall_len(side):
        return X if WALL_INFO[side]["axis"] == "x" else Y

    def add_opening(kind, oid, o):
        side = o["wall"]
        if side not in rects:
            err(f"{oid}: wall must be one of {list(rects)}, got {side!r}")
            return None
        width, ctr = ft(o["width"]), ft(o["center"])
        a0, a1 = ctr - width / 2.0, ctr + width / 2.0
        if kind == "window":
            z0, z1 = ft(o["sill_h"]), ft(o["head_h"])
        else:
            z0, z1 = 0.0, ft(o["height"])
        x0, y0, x1, y1 = rects[side]
        axis = WALL_INFO[side]["axis"]
        cut = (a0, y0 - 1.0, a1, y1 + 1.0) if axis == "x" else (x0 - 1.0, a0, x1 + 1.0, a1)
        mid = (faces_in[side] + faces_out[side]) / 2.0
        pthk = 0.25 if kind == "window" else 1.75
        panel = ((a0, mid - pthk / 2, z0, a1, mid + pthk / 2, z1) if axis == "x"
                 else (mid - pthk / 2, a0, z0, mid + pthk / 2, a1, z1))
        if a0 < -1e-6 or a1 > wall_len(side) + 1e-6:
            err(f"{oid}: opening {fmt_ftin(a0)}..{fmt_ftin(a1)} runs past the {side} wall (0..{fmt_ftin(wall_len(side))})")
        if z1 > H + 1e-6:
            err(f"{oid}: head at {fmt_ftin(z1)} is above the {fmt_ftin(H)} ceiling")
        if z0 >= z1 or width <= 0:
            err(f"{oid}: zero or negative opening size")
        op = {"id": oid, "kind": kind, "wall": side, "axis": axis, "inward": WALL_INFO[side]["inward"],
              "a0": a0, "a1": a1, "z0": z0, "z1": z1, "width": width, "center": ctr,
              "cut_rect": cut, "face_in": faces_in[side], "face_out": faces_out[side], "panel_box": panel}
        L["walls"]["wall_ext_" + side]["openings"].append(op)
        return op

    for wid, w in cfg["windows"].items():
        op = add_opening("window", wid, w)
        if op:
            L["windows"][wid] = op
    for did, d in cfg["doors"].items():
        op = add_opening("door", did, d)
        if op:
            op["hinge"] = d.get("hinge", "low")
            op["swing"] = d.get("swing", "in")
            op.update(_door_swing(op))
            L["doors"][did] = op

    # ------------------------------------------------------------------ bathroom + partitions
    b = cfg["bathroom"]
    by1_ft = _res(b["y1"], r["interior_y"])                 # AUTO: anchored to the top wall
    L["resolved"]["bathroom.y1"] = by1_ft
    bx0, bx1, by0, by1 = ft(b["x0"]), ft(b["x1"]), ft(b["y0"]), ft(by1_ft)
    L["bath"] = {"x0": bx0, "x1": bx1, "y0": by0, "y1": by1, "rect": (bx0, by0, bx1, by1)}
    if abs(bx0) > 1e-6 or abs(by1 - Y) > 1e-6:
        err("bathroom must sit in the top-left corner (x0 = 0, y1 = interior_y); the partition logic assumes it")
    if bx1 <= bx0 or by1 <= by0:
        err("bathroom x1 > x0 and y1 > y0 required")
    if by0 - it < 0:
        err("bathroom y0 leaves no room for the partition")
    has_right_part = bx1 + it < X - 1e-6          # a full-width bathroom has no x-partition
    pb_x1 = (bx1 + it) if has_right_part else X   # bottom partition runs to the outer face of the x-partition
    L["partitions"]["wall_int_bath_y"] = {"rect": (bx0, by0 - it, pb_x1, by0), "z": (0.0, H), "openings": [],
                                          "label": f"bathroom partition at y={fmt_ftin(by0)}"}
    if has_right_part:
        L["partitions"]["wall_int_bath_x"] = {"rect": (bx1, by0, bx1 + it, by1), "z": (0.0, H), "openings": [],
                                              "label": f"bathroom partition at x={fmt_ftin(bx1)}"}

    pd = b["pocket_door"]
    pw, ph = ft(pd["w"]), ft(pd["h"])
    px1 = ft(_res(pd["x1"], b["x1"]))                      # AUTO: right edge at the bathroom's x1
    if px1 > bx1 + 1e-6:
        warn(f"bathroom.pocket_door.x1 ({fmt_ftin(px1)}) is past bathroom.x1; clamped to {fmt_ftin(bx1)}")
        px1 = bx1
    L["resolved"]["bathroom.pocket_door.x1"] = px1 / IN_PER_FT
    px0 = px1 - pw
    slide = pd.get("slide", "left")
    pocket = (px0 - pw, by0 - it, px0, by0) if slide == "left" else (px1, by0 - it, px1 + pw, by0)
    # door panel drawn 30 % into the opening (2D symbol only)
    if slide == "left":
        panel = (px0 - 0.7 * pw, by0 - it / 2 - 0.75, px0 + 0.3 * pw, by0 - it / 2 + 0.75)
    else:
        panel = (px1 - 0.3 * pw, by0 - it / 2 - 0.75, px1 + 0.7 * pw, by0 - it / 2 + 0.75)
    if px0 < bx0 - 1e-6 or pocket[0] < bx0 - 1e-6 or pocket[2] > pb_x1 + 1e-6:
        err("pocket door + its pocket must fit inside the bathroom's bottom partition")
    if ph > H:
        err("pocket door taller than the ceiling")
    pdo = {"id": "PD1", "kind": "pocket_door", "axis": "x", "a0": px0, "a1": px1, "z0": 0.0, "z1": ph,
           "width": pw, "cut_rect": (px0, by0 - it - 1.0, px1, by0 + 1.0), "pocket_rect": pocket,
           "panel_rect": panel, "wall_rect": (bx0, by0 - it, pb_x1, by0), "face_in": by0, "face_out": by0 - it}
    L["partitions"]["wall_int_bath_y"]["openings"].append(pdo)
    L["pocket_door"] = pdo

    # ------------------------------------------------------------------ closet (bottom-right corner)
    cl = cfg["closet"]
    cw, cd, cdh, cdt = ft(cl["w"]), ft(cl["d"]), ft(cl["door_h"]), ft(cl["door_thk"])
    cx0 = X - cw
    L["closet"] = {"x0": cx0, "x1": X, "y0": 0.0, "y1": cd, "rect": (cx0, 0.0, X, cd)}
    if cx0 - it < 0:
        err("closet wider than the room")
    if cd - it <= 0:
        err("closet depth must exceed the partition thickness")
    if cdh > H:
        err("closet door taller than the ceiling")
    L["partitions"]["wall_int_closet"] = {"rect": (cx0 - it, 0.0, cx0, cd), "z": (0.0, H), "openings": [],
                                          "label": "closet side wall"}
    L["partitions"]["closet_door_header"] = {"rect": (cx0, cd - it, X, cd), "z": (cdh, H), "openings": [],
                                             "label": "closet door header"}
    yc = cd - it / 2.0
    L["closet_bifold"] = {"rect": (cx0, yc - cdt / 2, X, yc + cdt / 2), "z": (0.0, cdh),
                          "x0": cx0, "x1": X, "y_face": cd, "width": cw,
                          "label": f"BIFOLD {fmt_ftin(cw)}"}

    # ------------------------------------------------------------------ bathroom fixtures
    F = L["fixtures"]
    sh = b["shower"]
    shw, shd, shh = ft(sh["w"]), ft(sh["d"]), ft(sh["h"])
    F["fx_shower"] = {"box": (bx0, by1 - shd, 0.0, bx0 + shw, by1, shh), "cuts": [], "zone": "Bathroom",
                      "label": f"SHOWER {fmt_ftin(shw)} x {fmt_ftin(shd)}"}
    t = b["toilet"]
    tw, td, th, tyc = ft(t["w"]), ft(t["d"]), ft(t["h"]), ft(t["y_center"])
    F["fx_toilet"] = {"box": (bx0, tyc - tw / 2, 0.0, bx0 + td, tyc + tw / 2, th), "cuts": [], "zone": "Bathroom",
                      "label": "WC"}
    v = b["vanity"]
    vx0, vw, vd, vh = ft(v["x0"]), ft(v["w"]), ft(v["d"]), ft(v["h"])
    F["fx_vanity"] = {"box": (vx0, by0, 0.0, vx0 + vw, by0 + vd, vh), "cuts": [], "zone": "Bathroom",
                      "label": f"VANITY {fmt_ftin(vw)}"}

    # ------------------------------------------------------------------ kitchenette
    k = cfg["kitchenette"]
    face = (bx1 + it) if has_right_part else bx1           # nearest the counter can start (partition face)
    kx0 = ft(_res(k["x0"], face / IN_PER_FT))              # AUTO: at the partition face
    if kx0 < face - 1e-6:
        warn(f"kitchenette.x0 ({fmt_ftin(kx0)}) is inside the bathroom/partition; counter starts at the partition "
             f"face {fmt_ftin(face)} instead")
        kx0 = face
    kx1 = ft(_res(k["x1"], r["interior_x"]))               # AUTO: to the right wall
    if kx1 > X + 1e-6:
        warn(f"kitchenette.x1 ({fmt_ftin(kx1)}) is past the right wall; clamped to {fmt_ftin(X)}")
        kx1 = X
    L["resolved"]["kitchenette.x0"] = kx0 / IN_PER_FT
    L["resolved"]["kitchenette.x1"] = kx1 / IN_PER_FT
    cdp, ch, ctt = ft(k["counter_depth"]), ft(k["counter_h"]), ft(k["counter_top_thk"])
    fr = k["fridge"]
    fw, fh = ft(fr["w"]), ft(fr["h"])
    fx1 = ft(_res(fr["x1"], kx1 / IN_PER_FT))              # AUTO: at the right end of the counter
    if fx1 > kx1 + 1e-6:
        warn(f"kitchenette.fridge.x1 ({fmt_ftin(fx1)}) is past the end of the counter; clamped to {fmt_ftin(kx1)}")
        fx1 = kx1
    L["resolved"]["kitchenette.fridge.x1"] = fx1 / IN_PER_FT
    fx0 = fx1 - fw
    sk = k["sink"]
    sw, sd, sdep, sxc = ft(sk["w"]), ft(sk["d"]), ft(sk["depth"]), ft(sk["x_center"])
    sy0 = Y - cdp + (cdp - sd) / 2.0
    sink_box = (sxc - sw / 2, sy0, ch - sdep, sxc + sw / 2, sy0 + sd, ch)
    fridge_slot = (fx0, Y - cdp - 1.0, -1.0, fx1, Y + 1.0, ch - ctt)
    F["fx_counter"] = {"box": (kx0, Y - cdp, 0.0, kx1, Y, ch), "cuts": [fridge_slot, sink_box], "zone": "Kitchenette",
                       "label": f"COUNTER {fmt_ftin(cdp)} D x {fmt_ftin(ch)} H"}
    F["fx_sink"] = {"box": sink_box, "cuts": [], "zone": "Kitchenette", "label": "SINK"}
    F["fx_fridge"] = {"box": (fx0, Y - cdp, 0.0, fx1, Y, fh), "cuts": [], "zone": "Kitchenette", "label": "U/C REF"}
    L["kitchenette"] = {"x0": kx0, "x1": kx1, "y0": Y - cdp, "y1": Y, "counter_h": ch,
                        "zone_rect": (kx0, by0 - it, kx1, Y)}

    # ------------------------------------------------------------------ header (VERIFY LOAD-BEARING)
    hd = cfg["header"]
    hy = ft(_res(hd["y"], by0 / IN_PER_FT))                # AUTO: on the bathroom partition line
    hx0 = ft(_res(hd["x0"], bx1 / IN_PER_FT))              # AUTO: bears on the partition end
    hx1 = ft(_res(hd["x1"], r["interior_x"]))              # AUTO: bears on the right wall
    if hx1 > X + 1e-6:
        warn(f"header.x1 ({fmt_ftin(hx1)}) is past the right wall; clamped to {fmt_ftin(X)}")
        hx1 = X
    if hx0 >= hx1 - 1e-6:
        err(f"header.x0 ({fmt_ftin(hx0)}) must be left of header.x1 ({fmt_ftin(hx1)})")
        hx0, hx1 = min(hx0, hx1 - 1.0), hx1          # keep a positive length so later code does not crash
    L["resolved"].update({"header.y": hy / IN_PER_FT, "header.x0": hx0 / IN_PER_FT, "header.x1": hx1 / IN_PER_FT})
    hw, hdp = ft(hd["width"]), ft(hd["depth"])
    pc = (hy - it / 2.0) if abs(hy - by0) < 1e-6 else hy   # centre on the partition framing when on the bath line
    L["header"] = {"box": (hx0, pc - hw / 2, H - hdp, hx1, pc + hw / 2, H), "y_center": pc,
                   "line": ((hx0, pc), (hx1, pc)), "size": f"{fmt_in(hw)} x {fmt_in(hdp)}",
                   "label": f"HEADER {fmt_in(hw)} x {fmt_in(hdp)} - VERIFY LOAD-BEARING"}

    # ------------------------------------------------------------------ AC unit (reference), floor, ceiling
    ac = cfg["ac_unit"]
    ax0, ag, aw, ad, ah = ft(ac["x0"]), ft(ac["gap"]), ft(ac["w"]), ft(ac["d"]), ft(ac["h"])
    L["ac_unit"] = {"box": (ax0, -ext - ag - ad, 0.0, ax0 + aw, -ext - ag, ah),
                    "label": f"AC {fmt_ftin(aw)} x {fmt_ftin(ad)} x {fmt_ftin(ah)} (REF)"}
    slab = ft(cfg["floor"]["slab_thk"])
    L["floor"] = {"box": (-ext, -ext, -slab, X + ext, Y + ext, 0.0)}
    L["ceiling"] = {"box": (-ext, -ext, H, X + ext, Y + ext, H + ft(cfg["ceiling"]["thk"])),
                    "include": bool(cfg["ceiling"].get("include", False))}

    # ------------------------------------------------------------------ zones + areas
    living_rect = (0.0, 0.0, X, by0 - it)
    closet_block = (cx0 - it, 0.0, X, cd)          # closet + its side wall come out of the living zone
    Z = L["zones"]
    Z["Bathroom"] = {"rect": L["bath"]["rect"], "sqft": _area_sqft(L["bath"]["rect"])}
    Z["Kitchenette (open, under header)"] = {"rect": L["kitchenette"]["zone_rect"],
                                             "sqft": _area_sqft(L["kitchenette"]["zone_rect"])}
    Z["Living / sleeping (net of closet)"] = {"rect": living_rect,
                                              "sqft": _area_sqft(living_rect) - _area_sqft(closet_block)}
    Z["Closet"] = {"rect": L["closet"]["rect"], "sqft": _area_sqft(L["closet"]["rect"])}
    gross = X * Y / 144.0
    L["areas"] = {"interior_gross": gross,
                  "zones_total": sum(z["sqft"] for z in Z.values()),
                  "partitions": gross - sum(z["sqft"] for z in Z.values()),
                  "counter_footprint": _area_sqft((kx0, Y - cdp, kx1, Y))}

    # ------------------------------------------------------------------ the required assertion (feet, raw CONFIG)
    bath_depth = by1_ft - float(b["y0"])
    living_depth = float(b["y0"]) - 0.0
    diff = abs(bath_depth + living_depth - float(r["interior_y"]))
    L["zone_sum"] = {"bath_depth_ft": bath_depth, "living_depth_ft": living_depth,
                     "sum_ft": bath_depth + living_depth, "interior_y_ft": float(r["interior_y"]),
                     "diff_ft": diff, "tol_ft": float(wcfg["interior_thk"]),
                     "ok": diff <= float(wcfg["interior_thk"]) + 1e-9}
    if not L["zone_sum"]["ok"]:
        err(f"bathroom depth {bath_depth}' + living depth {living_depth}' = {bath_depth + living_depth}' "
            f"differs from interior_y {r['interior_y']}' by more than the {wcfg['interior_thk']}' partition")

    # ------------------------------------------------------------------ checks: openings
    for w in L["walls"].values():
        side = w["side"]
        ops = sorted(w["openings"], key=lambda o: o["a0"])
        for prev, nxt in zip(ops, ops[1:]):
            gap = nxt["a0"] - prev["a1"]
            if gap < -1e-6:
                err(f"{prev['id']} and {nxt['id']} overlap on the {side} wall")
            elif gap < MIN_JAMB:
                warn(f"{prev['id']} and {nxt['id']} meet on the {side} wall with only {fmt_in(gap)} of wall "
                     f"between them (no jamb / king studs) - check the window schedule")
        if ops:
            if ops[0]["a0"] < MIN_JAMB:
                warn(f"{ops[0]['id']} starts {fmt_in(ops[0]['a0'])} from the corner of the {side} wall "
                     f"(< {fmt_in(MIN_JAMB)}) - no room for a corner post")
            if wall_len(side) - ops[-1]["a1"] < MIN_JAMB:
                warn(f"{ops[-1]['id']} ends {fmt_in(wall_len(side) - ops[-1]['a1'])} from the corner of the "
                     f"{side} wall (< {fmt_in(MIN_JAMB)}) - no room for a corner post")

    def junction_check(what, side, lo, hi):
        for op in L["walls"]["wall_ext_" + side]["openings"]:
            if op["a0"] < hi - 1e-6 and op["a1"] > lo + 1e-6:
                warn(f"{what} meets the {side} wall at {fmt_ftin(lo)}..{fmt_ftin(hi)}, inside opening "
                     f"{op['id']} ({fmt_ftin(op['a0'])}..{fmt_ftin(op['a1'])}) - the opening needs a jamb there")

    junction_check(f"bathroom partition (y={fmt_ftin(by0)})", "left", by0 - it, by0)
    if has_right_part:
        junction_check(f"bathroom partition (x={fmt_ftin(bx1)})", "top", bx1, bx1 + it)
    junction_check("closet side wall", "bottom", cx0 - it, cx0)
    junction_check("closet door header", "right", cd - it, cd)

    # ------------------------------------------------------------------ checks: bathroom fixtures
    bath_rect = L["bath"]["rect"]
    bath_fix = {n: (f["box"][0], f["box"][1], f["box"][3], f["box"][4]) for n, f in F.items() if f["zone"] == "Bathroom"}
    for n, rect in bath_fix.items():
        if not _inside(rect, bath_rect):
            err(f"{n} ({fmt_ftin(rect[0])},{fmt_ftin(rect[1])})..({fmt_ftin(rect[2])},{fmt_ftin(rect[3])}) is outside the bathroom")
    names = list(bath_fix)
    for i, a in enumerate(names):
        for bname in names[i + 1:]:
            if _overlap(bath_fix[a], bath_fix[bname]):
                err(f"{a} overlaps {bname}")
    side_to_shower = (by1 - shd) - tyc
    side_to_part = tyc - by0
    if side_to_shower < TOILET_SIDE_CLR - 1e-6:
        warn(f"toilet centreline is {fmt_in(side_to_shower)} from the shower (IRC wants {fmt_in(TOILET_SIDE_CLR)})")
    if side_to_part < TOILET_SIDE_CLR - 1e-6:
        warn(f"toilet centreline is {fmt_in(side_to_part)} from the partition (IRC wants {fmt_in(TOILET_SIDE_CLR)})")
    front = (bx0 + td, tyc - tw / 2, bx0 + td + TOILET_FRONT_CLR, tyc + tw / 2)
    for n in ("fx_shower", "fx_vanity"):
        if _overlap(front, bath_fix[n]):
            warn(f"{n} intrudes into the {fmt_in(TOILET_FRONT_CLR)} clearance in front of the toilet")
    if pocket[0] < bath_fix["fx_vanity"][2] - 1e-6 and pocket[2] > bath_fix["fx_vanity"][0] + 1e-6:
        warn(f"pocket-door pocket ({fmt_ftin(pocket[0])}..{fmt_ftin(pocket[2])}) sits behind the vanity "
             f"({fmt_ftin(bath_fix['fx_vanity'][0])}..{fmt_ftin(bath_fix['fx_vanity'][2])}): no supply/drain can run "
             f"in that wall section - feed the vanity from the floor or shift it")
    if pdo["a0"] < bath_fix["fx_vanity"][2] - 1e-6 and pdo["a1"] > bath_fix["fx_vanity"][0] + 1e-6:
        err("pocket door opening overlaps the vanity")

    # ------------------------------------------------------------------ checks: kitchenette / closet / header
    counter_rect = (kx0, Y - cdp, kx1, Y)
    if kx0 >= kx1:
        err("kitchenette counter has zero length")
    if _overlap(counter_rect, bath_rect):
        err("kitchen counter overlaps the bathroom interior")
    for pname, prt in L["partitions"].items():
        if prt["z"][0] < 1e-6 and _overlap(counter_rect, prt["rect"]):
            err(f"kitchen counter overlaps {pname}")
    if not _inside((sink_box[0], sink_box[1], sink_box[3], sink_box[4]), counter_rect):
        err(f"sink ({fmt_ftin(sink_box[0])}..{fmt_ftin(sink_box[3])}) is not inside the counter "
            f"({fmt_ftin(kx0)}..{fmt_ftin(kx1)}) - move kitchenette.sink.x_center")
    if fx0 < kx0 - 1e-6:
        err("fridge runs past the left end of the counter")
    if fh > ch - ctt + 1e-6:
        warn(f"fridge ({fmt_in(fh)} tall) does not fit under the {fmt_in(ch - ctt)} counter-top underside")
    if _overlap((sink_box[0], sink_box[1], sink_box[3], sink_box[4]), (fx0, Y - cdp, fx1, Y)):
        err("sink overlaps the fridge")
    if abs(hx0 - bx1) > 1e-6 or abs(hx1 - X) > 1e-6:
        warn(f"header spans {fmt_ftin(hx0)}..{fmt_ftin(hx1)} but should bear on the bathroom partition end "
             f"({fmt_ftin(bx1)}) and the right wall ({fmt_ftin(X)}) - set header.x0/x1 to 'auto'")
    if abs(hy - by0) > 1e-6:
        warn(f"header.y ({fmt_ftin(hy)}) is not on the bathroom partition line ({fmt_ftin(by0)}); the beam will "
             f"float in the room - set header.y to 'auto'")
    for did, op in L["doors"].items():
        if op["wall"] == "right" and op["a0"] < cd + 1e-6:
            warn(f"{did} opening overlaps the closet on the right wall")
    return L


def _fmt_cfg(v, path: str, resolved: dict, top: bool = True) -> str:
    """Compact, human-readable CONFIG value; 'auto' shows what it resolved to."""
    if _is_auto(v):
        rv = resolved.get(path)
        return "auto" if rv is None else f"auto -> {fmt_ftin(ft(rv))}"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{v:g} ft ({fmt_ftin(ft(v))})" if top else fmt_ftin(ft(v))
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_fmt_cfg(x, f'{path}.{k}', resolved, top=False)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + "; ".join(_fmt_cfg(x, path, resolved, top=False) for x in v) + "]"
    return repr(v)


def assumption_rows(cfg: dict = CONFIG, L: dict | None = None, assumptions: dict = ASSUMPTIONS):
    """(config key, current value text, note) for every flagged CONFIG entry - values are read live."""
    resolved = (L or {}).get("resolved", {})
    rows = []
    for key, note in assumptions.items():
        node, found = cfg, True
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                found = False
                break
        rows.append((key, _fmt_cfg(node, key, resolved) if found else "(no such CONFIG key)", note))
    return rows


def summary_rows(L: dict) -> list[tuple[str, str, float]]:
    """(zone, footprint text, sqft) rows for the build summary."""
    rows = []
    for name, z in L["zones"].items():
        x0, y0, x1, y1 = z["rect"]
        rows.append((name, f"{fmt_ftin(x1 - x0)} x {fmt_ftin(y1 - y0)}", z["sqft"]))
    return rows
