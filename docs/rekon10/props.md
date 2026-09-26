# Propellers, by label

Props are labelled near the hub in sharpie so an individual prop can be tracked across flights
and remounts. This file is the ledger: which set flew when, and what is known or suspected about
each. It exists because a prop change alters the whole vibration signature, and an analysis that
does not know which set was fitted cannot read a spectrum.

**Blade count is the single most consequential fact here for analysis.** It sets where blade-pass
falls: blade-pass = blades x rev. On three blades a rev line at 87 Hz puts blade-pass at 261 Hz;
on two blades it puts it at 174 Hz. Mislabelling the 2nd harmonic as blade-pass on a three-blade
aircraft moves a named line by a third of its frequency, which is enough to point at the wrong
mechanism.

## Current: set B, three-blade

| | |
|---|---|
| labels | B1, B2, B3, B4 |
| part | **HQ MacroQuad 10x4.8x3** -- 10 in diameter, 4.8 in pitch, **3 blades** |
| material | glass-fibre reinforced nylon |
| shaft | 5 mm, fits the shaft directly with **no collar** |
| supplier | [hqprop.com p0490](https://www.hqprop.com/hq-macroquad-prop-10x48x32cw2ccw-black-glass-fiber-reinforced-nylon-p0490.html) (2CW + 2CCW) |
| first flight | **260923** (`260923-new-props`) |

## Retired: set A

| | |
|---|---|
| labels | A1, A2, A3, A4 |
| status | removed from the airframe and labelled, retained |
| what they carry | **the only vibration data predating the ADXL345 pods.** Every flight before 260923 is set A |
| history | flew through the 260712 crash |

**The set change is a natural experiment that already ran, and its result is about the 1x line.**
A strong line at 1x rotation is the textbook prop-imbalance signature. That line is present on
set B, which is four new props mounted fresh -- so it is not coming from a particular set's
balance state, because four independent new props do not share one imbalance. Replacing every
prop did not remove it.

What that leaves, if the 1x line is to be an eccentricity at all, is the motor side rather than
the prop side -- a bell running off centre. Nothing here measures that, and it is not being
claimed; it is named only because eliminating the props narrows what is left.

## Blade count by era

**Every flight from early July 2026 onward is three-bladed.** The two-blade set was broken in early
July and nothing two-bladed has flown since. Analyses that assume two blades are wrong for every
flight in that range -- and that assumption has been made: see the note in
[vibration-testing.md](vibration-testing.md) on the 2x-rev-vs-blade-pass labelling.

A **two-blade set is on hand and has not been flown.** Flying it is the test that separates
blade-rate effects from rotational-harmonic effects, because it moves blade-pass without moving
rev. That distinction is currently unresolved and is why the set was kept.

## What to record when a set changes

So that the next analysis does not have to infer it:

- Labels of the props fitted, and which position each went to.
- Diameter, pitch, **blade count**, material, hub/shaft fit.
- Whether this is the set's first flight.
- Anything known about their history: crashed, reused, rebalanced, or new.
