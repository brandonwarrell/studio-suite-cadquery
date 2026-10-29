"""
CONFIG for the studio-suite model.   *** ALL VALUES ARE IN FEET (decimal feet). ***
Edit this file only; layout.py / model.py / floorplan.py / build.py read everything from here
and convert to inches internally.

COORDINATE SYSTEM
  Origin  = bottom-left INTERIOR corner of the room, at floor level.
  X       = along the 15' wall (left -> right)
  Y       = along the 21' wall (bottom -> top)
  Z       = up
  "top wall" = the Y = interior_y side, "left wall" = the X = 0 side.

For windows/doors, "center" is the position ALONG the wall: an X value for the top/bottom
walls, a Y value for the left/right walls.  Interior partitions sit on the LIVING side of the
bathroom lines (y0 / x1) so the bathroom keeps its full (x1-x0) by (y1-y0) interior.

"auto" (AUTO) is allowed on the keys marked below: the value then follows its parent dimension
(e.g. header.x1 = interior_x) so that changing one headline number moves everything that hangs
off it.  Put a number there to override; build.py prints the resolved value either way.
"""

AUTO = "auto"

CONFIG = {
    "room": {
        "interior_x": 15.0,          # interior width  (X)
        "interior_y": 21.0,          # interior length (Y)
        "ceiling_h": 8.0,            # ASSUMPTION - not on the sketch
    },
    "walls": {
        "exterior_thk": 0.5,         # ASSUMPTION - 2x6 + sheathing + drywall ~ 6"
        "interior_thk": 0.375,       # 2x4 + 1/2" drywall both sides = 4.5"
    },
    "floor": {
        "slab_thk": 4 / 12,          # thin floor plate under the whole footprint (ASSUMPTION, massing only)
    },
    "ceiling": {
        "include": False,            # True -> add a ceiling plate (handy for an enclosed UE5 room)
        "thk": 0.5 / 12,
    },

    # ---------------------------------------------------------------- bathroom (top-left)
    "bathroom": {
        "x0": 0.0, "x1": 8.0,        # 8' wide (X)
        "y0": 14.0,                  # 7' deep (Y) ...
        "y1": AUTO,                  # sketch: 21 = interior_y; must stay equal to interior_y (anchored to the top wall)
        # 3x5 shower "in the top-left corner, along the top wall": w runs along X (the top wall), d along Y.
        # Swap w/d to turn it 90 degrees.
        "shower": {"w": 5.0, "d": 3.0, "h": 7.0},
        # toilet backs onto the LEFT wall, below the shower. w = width along Y, d = projection into the room (X).
        # y_center = 16.75 puts its centerline 15" off the shower and keeps the vanity out of its front clearance.
        "toilet": {"w": 1.5, "d": 2.5, "h": 2.5, "y_center": 16.75},
        # vanity sits against the bathroom's bottom partition (y0), right of the toilet.
        "vanity": {"x0": 2.5, "w": 3.0, "d": 2.0, "h": 34 / 12},
        # pocket door in the bottom partition; x1 = its right edge (sketch: "near x1" -> AUTO = bathroom.x1).
        "pocket_door": {"w": 2.5, "h": 6 + 8 / 12, "x1": AUTO, "slide": "left"},
    },

    # ---------------------------------------------------------------- kitchenette (top wall, x = 8..15)
    "kitchenette": {
        "x0": AUTO,                  # sketch: 8  -> AUTO = face of the bathroom partition (bathroom.x1 + interior_thk)
        "x1": AUTO,                  # sketch: 15 -> AUTO = interior_x (the right wall)
        "counter_depth": 2.0,
        "counter_h": 3.0,
        "counter_top_thk": 1.5 / 12,
        "sink": {"x_center": 10.0, "w": 2.5, "d": 1.5, "depth": 8 / 12},
        "fridge": {"w": 2.0, "h": 34 / 12, "x1": AUTO},      # under-counter; sketch: "near the right end" -> AUTO = kitchenette.x1
    },

    # ---------------------------------------------------------------- closet (bottom-right corner)
    "closet": {
        "w": 4.0,                    # along X
        "d": 2.0,                    # along Y
        "door_h": 6 + 8 / 12,        # bifold height
        "door_thk": 1.5 / 12,        # bifold panel thickness (massing)
    },

    # ---------------------------------------------------------------- openings (exterior walls)
    "windows": {
        "W1": {"wall": "top", "center": 4.0,  "width": 8.0, "sill_h": 3.5, "head_h": 6.5},   # over the bathroom
        "W2": {"wall": "top", "center": 11.5, "width": 7.0, "sill_h": 3.5, "head_h": 6.5},   # over the kitchenette
    },
    "doors": {   # exterior doors 3'-0" x 6'-8".  hinge: "high"/"low" = which end of the opening (along the wall).
        "D1": {"wall": "left",  "center": 12.0, "width": 3.0, "height": 6 + 8 / 12, "hinge": "high", "swing": "in"},
        "D2": {"wall": "right", "center": 5.0,  "width": 3.0, "height": 6 + 8 / 12, "hinge": "low",  "swing": "in"},
    },

    # ---------------------------------------------------------------- header beam (VERIFY LOAD-BEARING)
    "header": {
        "y": AUTO,                   # sketch: 14 -> AUTO = bathroom.y0 (centred on the partition framing)
        "x0": AUTO,                  # sketch: 8  -> AUTO = bathroom.x1 (bears on the partition end)
        "x1": AUTO,                  # sketch: 15 -> AUTO = interior_x  (bears on the right wall)
        "width": 3.5 / 12,           # plan width  (3.5")
        "depth": 9.25 / 12,          # vertical depth (9.25"), hung from the ceiling
    },

    # ---------------------------------------------------------------- exterior AC unit (reference only)
    "ac_unit": {"x0": 0.5, "gap": 0.5, "w": 3.0, "d": 3.0, "h": 3.0},   # outside the bottom wall, at the left corner

    # ---------------------------------------------------------------- export options
    "export": {
        "stl_units": "in",           # "in" | "mm" | "cm" | "m"  (STL has no unit tag; STEP is tagged INCH)
        "mesh_tolerance": 0.02,      # tessellation tolerance (inches)
        "include_window_glass": False,   # extra thin panes in the window openings (separate solids)
        "include_door_leaves": False,    # extra door leaf solids, drawn closed
        # GLB variants written by build.py.  UE5's glTF importer ALWAYS multiplies by 100 (m -> cm) and swaps
        # Y/Z, so the file that lands in Unreal as Z-up centimeters is the glTF-standard one (meters, Y-up).
        # The *_zup_cm file has the vertices literally in centimeters, Z-up, for tools that want raw coordinates.
        "glb_variants": [
            {"file": "studio_suite.glb",        "units": "m",  "up": "Y"},   # <- import THIS into UE5
            {"file": "studio_suite_zup_cm.glb", "units": "cm", "up": "Z"},
        ],
    },
}

# CONFIG keys that are guesses rather than sketch values, or that need a site check.  build.py prints
# each key with its CURRENT value (and the resolved value for "auto" keys) next to the note below.
ASSUMPTIONS = {
    "room.ceiling_h":        "not on the sketch - ceiling height assumed",
    "walls.exterior_thk":    "assumed 2x6 + sheathing + drywall",
    "walls.interior_thk":    "assumed 2x4 + 1/2\" drywall each side",
    "floor.slab_thk":        "floor plate thickness assumed - massing only",
    "header":                "VERIFY LOAD-BEARING - width x depth is a placeholder size; y/x0/x1 follow the bathroom "
                             "partition line, its end and the right wall while 'auto'",
    "doors.D1.center":       "sketch says y~12.5; default lowered so the 3' opening clears the bathroom partition face "
                             "(a 12.5 centre puts the jamb inside the partition - see the D1 range under OBJECTS)",
    "bathroom.shower":       "'3 x 5 along the top wall' read as w along X (the top wall) by d along Y; swap w/d to turn it",
    "bathroom.toilet":       "size and y_center chosen for 15\" side / 21\" front clearance (checked - see warnings)",
    "bathroom.vanity":       "width and height assumed - the sketch only gives the 2' depth",
    "kitchenette.sink":      "sink size assumed; only 'centered ~x=10' is from the sketch",
    "kitchenette.fridge":    "under-counter fridge height assumed; width and 'near the right end' are from the sketch",
    "closet":                "bifold door height/thickness assumed; footprint is from the sketch",
    "windows":               "as sketched W1 and W2 meet at bathroom.x1 with no jamb between them and run into both "
                             "corners - check against the window schedule (see warnings)",
    "ac_unit":               "reference box only; 'outside the bottom-left corner' placed just off the bottom wall",
    "export.glb_variants":   "studio_suite.glb is glTF-standard (m, Y-up) so UE5 imports it as Z-up cm; "
                             "studio_suite_zup_cm.glb holds raw Z-up centimeters",
}
