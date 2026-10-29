"""studio_suite - parametric CAD model of a 15' x 21' room addition converted to a studio suite.

Modules
-------
config     CONFIG dict (all values in FEET) + ASSUMPTIONS flags   <- the only file you should need to edit
layout     derives every rectangle/box in INCHES from CONFIG, runs validation checks (pure Python, no CadQuery)
model      3D build with CadQuery + STEP / STL / GLB / iso.png exports
floorplan  2D plan -> DXF (ezdxf), then SVG + plan.png rendered from that same DXF
build      runs everything, writes ./output, prints the summary table
"""
__version__ = "1.0.0"
