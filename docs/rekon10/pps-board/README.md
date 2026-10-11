# PPS board generators

`netlist.py` is the source of truth for the PPS distribution board -- every part and
every net. The other two scripts read it and write the artifacts, so the layout, the
diagram and the net table cannot drift from each other or from the netlist.

| run | writes | what it does |
|-----|--------|--------------|
| `python3 layout.py` | the fenced block in [`../central-hub.md`](../central-hub.md) | builds the protoboard layout and **verifies** it: every placed net connected, no hole carrying two nets, no orphaned pin. Exits with the problems listed if any of that fails. |
| `python3 draw.py` | [`../pps-protoboard.svg`](../pps-protoboard.svg) | renders the same layout component-side. Imports `layout.py`, so running it alone regenerates both. |

Run `layout.py` first; `draw.py` re-runs it anyway. Neither takes arguments and both
resolve paths relative to themselves, so the working directory does not matter.

Only the fenced block inside the **Protoboard layout** section is machine-written. The
prose around it in `central-hub.md` is maintained by hand and is not touched.

## Reading the diagram

Thick short segments are bridges, thin lines are jumpers, colour is the net. Runs that
share a column or a row are bowed apart so they do not overprint -- the bow is drawing,
not routing.

Two-lead parts are drawn as their schematic symbols along the line between their two
holes: a box for a resistor, two plates for a capacitor, a triangle and bar for the LED
with the bar at the cathode. Violet rectangles are the offboard pigtail footprints, red
crosses the no-connects, violet dashes the nets with no landing yet.

## The schematic is not generated from here

[`../pps-board.kicad_sch`](../pps-board.kicad_sch) was originally produced from this same
`netlist.py` via `kiutils`, but that generator is deliberately not committed: once the
sheet has been opened and tidied in KiCad, regenerating would throw that away.

**So the schematic will not follow a change to `netlist.py`.** If you change the netlist,
the layout and the SVG update on the next run and the schematic does not -- edit it in
KiCad to match, or check the two against each other before trusting either. The net table
in `central-hub.md` is generated from the netlist and is the tiebreaker.
