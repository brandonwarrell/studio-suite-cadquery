"""
Build everything and write ./output.

    python -m studio_suite.build          (from the folder that contains studio_suite/)
    python studio_suite/build.py          (also works)

Steps: derive + validate layout -> CadQuery model -> STEP / STL / GLB (x2) / iso.png
       -> DXF floor plan -> SVG + plan.png -> summary table -> confirm every output file exists.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio_suite.config import CONFIG                                        # noqa: E402
from studio_suite.layout import derive, fmt_ftin, summary_rows, assumption_rows  # noqa: E402

OUT_DIR = ROOT / "output"
EXPECTED = ["studio_suite.step", "studio_suite.stl", "floorplan.dxf", "floorplan.svg", "iso.png", "plan.png"]


def _banner(msg):
    print(f"\n=== {msg} " + "=" * max(0, 70 - len(msg)))


def validate(L):
    """Everything layout.derive flagged as an ERROR, then the required zone-sum assertion."""
    errors = [m for lvl, m in L["checks"] if lvl == "ERROR"]
    if errors:
        raise SystemExit("layout ERRORS:\n  " + "\n  ".join(errors))
    zs = L["zone_sum"]
    assert zs["ok"], (f"bathroom depth {zs['bath_depth_ft']}' + living depth {zs['living_depth_ft']}' = {zs['sum_ft']}' "
                      f"!= interior_y {zs['interior_y_ft']}' (tolerance {zs['tol_ft']}')")
    print(f"zone check: bathroom {zs['bath_depth_ft']:g}' + living {zs['living_depth_ft']:g}' = {zs['sum_ft']:g}' "
          f"== interior_y {zs['interior_y_ft']:g}'  (|diff| {zs['diff_ft']:g}' <= partition {zs['tol_ft']:g}')  OK")


def print_summary(L, parts):
    _banner("SUMMARY")
    rows = summary_rows(L)
    w = max(len(r[0]) for r in rows) + 2
    print(f"{'ZONE':<{w}}{'FOOTPRINT':<22}{'SQ FT':>8}")
    print("-" * (w + 30))
    for name, fp, sqft in rows:
        print(f"{name:<{w}}{fp:<22}{sqft:>8.1f}")
    print("-" * (w + 30))
    a = L["areas"]
    print(f"{'zones total':<{w}}{'':<22}{a['zones_total']:>8.1f}")
    print(f"{'partition footprint':<{w}}{'':<22}{a['partitions']:>8.1f}")
    print(f"{'interior gross':<{w}}{fmt_ftin(L['X']) + ' x ' + fmt_ftin(L['Y']):<22}{a['interior_gross']:>8.1f}")
    print(f"{'kitchen counter footprint':<{w}}{'':<22}{a['counter_footprint']:>8.1f}")

    print("\nOBJECTS (one named solid each):")
    for p in parts:
        x0, y0, z0, x1, y1, z1 = p.bbox
        print(f"  {p.name:<22} {p.category:<9} {fmt_ftin(x1 - x0):>10} x {fmt_ftin(y1 - y0):>10} x {fmt_ftin(z1 - z0):>10}"
              f"   @ ({x0 / 12:.3f}', {y0 / 12:.3f}', {z0 / 12:.3f}')")

    print("\nRESOLVED 'auto' KEYS (feet):")
    for key, val in L["resolved"].items():
        print(f"  {key:<28} = {val:g} ft ({fmt_ftin(val * 12)})")

    print("\nASSUMPTIONS / FLAGS (current CONFIG values - edit studio_suite/config.py):")
    for key, value, note in assumption_rows(CONFIG, L):
        print(f"  [{key}] = {value}")
        print(f"      {note}")
    warns = [m for lvl, m in L["checks"] if lvl == "WARN"]
    print(f"\nLAYOUT WARNINGS ({len(warns)}):")
    for m in warns:
        print(f"  ! {m}")


def readback(out_dir, parts):
    """Open the exports again and make sure names / units / bounds survived."""
    _banner("READBACK")
    names = {p.name for p in parts}
    step = (out_dir / "studio_suite.step").read_text(encoding="utf-8", errors="ignore")
    missing = [n for n in names if n not in step]
    print(f"STEP: {len(names) - len(missing)}/{len(names)} part names present; unit tag INCH: {'INCH' in step}")
    if missing:
        raise SystemExit(f"STEP is missing product names: {missing}")
    import trimesh
    for v in CONFIG["export"]["glb_variants"]:
        sc = trimesh.load(out_dir / v["file"], force="scene")
        got = set(sc.geometry.keys())
        lo, hi = sc.bounds
        print(f"GLB {v['file']}: {len(got)} meshes, bounds min {lo.round(3).tolist()} max {hi.round(3).tolist()} "
              f"[{v['units']}, +{v['up']} up]  names ok: {got == names}")
        if got != names:
            raise SystemExit(f"GLB {v['file']} mesh names differ: {sorted(got ^ names)}")
    stl = trimesh.load(out_dir / "studio_suite.stl", force="mesh")
    print(f"STL: {len(stl.faces)} triangles, extents (in) {stl.extents.round(2).tolist()}")
    import ezdxf
    doc = ezdxf.readfile(out_dir / "floorplan.dxf")
    msp = doc.modelspace()
    kinds = {}
    for e in msp:
        kinds[e.dxftype()] = kinds.get(e.dxftype(), 0) + 1
    dims = [e for e in msp if e.dxftype() == "DIMENSION"]
    print(f"DXF: $INSUNITS={doc.header['$INSUNITS']} (1 = inches), entities {dict(sorted(kinds.items()))}")
    print(f"DXF dimension texts: {[d.dxf.text for d in dims]}")


def main(out_dir: Path = OUT_DIR) -> int:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    exp = CONFIG["export"]

    _banner("LAYOUT + VALIDATION")
    L = derive(CONFIG)
    validate(L)

    _banner("3D MODEL (CadQuery)")
    from studio_suite import model
    parts, L = model.build_model(CONFIG)
    print(f"built {len(parts)} solids in {time.time() - t0:.1f}s")
    meshes = model.tessellate_parts(parts, tolerance=exp["mesh_tolerance"])
    print("wrote", model.export_step(parts, out_dir / "studio_suite.step"))
    print("wrote", model.export_stl(parts, out_dir / "studio_suite.stl", units=exp["stl_units"],
                                    tolerance=exp["mesh_tolerance"]))
    glb_files = []
    for v in exp["glb_variants"]:
        p = model.export_glb(parts, meshes, out_dir / v["file"], units=v["units"], up=v["up"])
        glb_files.append(v["file"])
        print("wrote", p, f"[{v['units']}, +{v['up']} up]")
    print("wrote", model.render_iso(parts, meshes, out_dir / "iso.png", L=L))

    _banner("2D FLOOR PLAN (ezdxf)")
    from studio_suite import floorplan
    for p in floorplan.export_floorplan(CONFIG, out_dir):
        print("wrote", p)

    print_summary(L, parts)
    readback(out_dir, parts)

    _banner("OUTPUT FILES")
    missing = []
    for name in EXPECTED + glb_files:
        p = out_dir / name
        ok = p.exists() and p.stat().st_size > 0
        print(f"  {'OK ' if ok else 'MISSING'}  {name:<26} {p.stat().st_size if ok else 0:>10,d} bytes")
        if not ok:
            missing.append(name)
    if missing:
        raise SystemExit(f"missing outputs: {missing}")
    print(f"\nALL OUTPUTS PRESENT in {out_dir}  ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as e:              # our own validation failures
        print(f"\nBUILD FAILED: {e}")
        code = 1
    except Exception:
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    # cadquery-ocp 7.9.3 on Python 3.14 (Windows) access-violates while the interpreter tears down its
    # OCCT modules, after all work is done and all files are closed.  os._exit skips that teardown so
    # the process exit code reflects the build result, not the crash.
    os._exit(code)
