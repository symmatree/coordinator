# Propellers, by label

Props are labelled near the hub in sharpie so an individual prop can be followed across flights and
remounts. This file records **which sets exist and what they are**.

**Which set flew a given flight belongs in that flight's own notes**, not here -- "this flight was on
set B" is one line in the flight README and it is written when the flight happens. Trying to maintain
a flights-to-props mapping in this file is a losing battle and would go stale immediately.

## Set B

Manufacturer designation **HQ MacroQuad 10x4.8x3**. By the usual convention that is diameter 10 in,
pitch 4.8, 3 blades -- the pitch unit is not verified against HQProp's own specification and may not
be inches.

- Glass-fibre reinforced nylon
- 5 mm shaft, fits the shaft directly with no collar
- 2 CW + 2 CCW
- [HQProp's page](https://www.hqprop.com/hq-macroquad-prop-10x48x32cw2ccw-black-glass-fiber-reinforced-nylon-p0490.html)
  -- the **manufacturer** link, not where they were bought. Its value here is HQProp's own test data
  for lift against RPM, which should be comparable with their other props' figures.
- Labelled B1-4. **Which label is on which arm is not recorded here** and would be the useful thing to
  have; it can be read off the vehicle.

## Set A

Labelled A1-4, kept. On the airframe for every flight before set B went on, including the 260712
crash.

They were swapped out because I argued that the strong line at motor rev rate was evidence of
imbalance or a chipped blade, and they had in fact been in a crash. **That argument is now much
weaker:** set B is a new set and a somewhat different prop, and shows very similar data. Set A may go
back on.

## Blade count

Set A and set B are both three-blade. **This is a fact about particular flights, not a standing
property of the airframe** -- there are two- and three-blade props on hand specifically to test which
peaks move with blade count, because with everything having a fundamental somewhere between roughly
100 and 250 Hz and harmonics above that, numerical coincidences are easy to come by.

Blade-pass is blade count x rev, so it matters which set flew: at three blades a 87 Hz rev line puts
blade-pass at 261 Hz, at two blades 174 Hz. Analysis that assumes a blade count without checking the
flight notes can name the wrong mechanism.
