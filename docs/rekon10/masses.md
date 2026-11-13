# Masses, as weighed

Ground truth for the mass budget, weighed on a scale that reads to 0.5 g. Each row is what was
on the scale; where an item includes others, the row says so.

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
