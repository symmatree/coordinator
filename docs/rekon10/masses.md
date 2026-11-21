# Masses, as weighed

Ground truth for the mass budget, weighed on a scale that reads to 0.5 g. Each row is what was
on the scale; where an item includes others, the row says so.

## 2026-11-21, rebuilding the flight platform with the replacement ESC

| item | mass (g) | what is included |
|---|---|---|
| ESC | 50.0 | the replacement TBS Lucid 4-in-1, with its motor-lead XT30 pigtails and battery pigtail; no mounting hardware |
| Power stack | 105 | the ESC row above, plus the payload UBEC, the spark stopper, the PM02D and the wires between them; no mounting hardware |
| FC | 18.0 | the Lucid H7 with all its pigtails; no mounting hardware |
| Frame, without top plate or GPS | 775.5 | the frame with its four motors and their pigtails; the raft's rubber mounts; the VTX, its camera, antennas and wiring; both radios and their antennas; the TPU mounts for the VTX and antennas; the GPS protoboard and pigtails. Not included: the FC, the top plate, the GPS mast, the GPS |
| Frame, with top plate and mast | 828 | the row above, plus the top plate, two battery straps, the mast base mount and the mast |
| Top plate, battery straps and mast | 52.5 | the top plate, two battery straps, the mast base mount (bolted to the top plate) and the mast; the 828 g row less the 775.5 g row |

### Derived

| item | mass (g) | from |
|---|---|---|
| UBEC, spark stopper, PM02D and their wires | 55 | 105 - 50.0 |

## 2026-11-13, with the airframe in parts after the 2026-11-07 crash

| item | mass (g) | what is included |
|---|---|---|
| Arducam M12 camera module | 8 | the module alone |
| Arducam M12 module on its mount | 11.5 | module, printed mount, heat-set inserts |
| 1/4-20 bolts, pair | 6.5 | the two bolts that hold one camera mount to the raft |
| Cheese plate with three Arducam kits | 384.5 | the cheese plate with three Arducam modules, each with its mount, inserts and bolts; nothing else on it -- no landing gear, no compute |
| GPS unit | 112.5 | the F9P unit with its cable; no mast, no base |
| Accelerometer | 4.5 | one ADXL345 with its pigtail |
| Flight platform, most of it | 906 | arms, motors, bottom and middle plates, VTX, FC, ESC, both radios, all wiring and antennas, battery leads and the XT60s for the spark stopper and the PM02D. Not included: GPS (above), top plate, battery |
| Battery, 6 Ah Li-ion | 600.5 | the smaller pack |

### Derived

| item | mass (g) | from |
|---|---|---|
| Cheese plate alone | 330.5 | 384.5 - 3 x (11.5 + 6.5), assuming the three camera kits are identical |

Each reading is to the nearest 0.5 g (+/-0.25 g). The derivation uses three readings, two of
them multiplied by three, so rounding alone can move it by up to 0.25 + 3 x 0.25 + 3 x 0.25 =
+/-1.75 g.
