# Operating envelopes: where each capability holds, by performance tier

*A map of belief, like `vio-quality-experiments.md` and `sitl-validation-experiments.md`, but organized
around a different question. Those ledgers ask **"how good can this capability get, and why?"** — which
quietly conflates two things: the **best achievable** (when you already know the answer and tune toward
it) and **what is possible** (open-ended discovery). This doc asks the operational inverse:*

> **For each discrete level of a capability, what is the environmental / operational envelope in which
> it holds — and which of the drivers that bound that envelope can we push to widen it?**

The payoff of the flip: it turns a capability into an **operational map** ("can I fly *this* mission in
*these* conditions?") instead of a single quality number, and it makes **"expand the envelope"** a
concrete engineering target (identify a boundary, find the driver, push the controllable ones). It also
forces honesty about **where levels overlap or fail together** — the most important structure for a
GPS-denied-fallback aircraft (see the cross-coupling section, which is the load-bearing finding so far).

## How this doc works

For each **capability** we define:

1. **Tiers** — discrete, *observable* performance levels, ordered, each with the **metric that defines its
   boundary** (so a flight log is a set of `(conditions → tier)` samples, not a vibe).
2. **Driver axes** — the environmental / operational dimensions that determine which tier holds. Each axis
   is tagged **[fixed]** (a given we design around), **[operational]** (we choose it per flight: where,
   when, how we fly), or **[controllable]** (an engineering lever we can change to move the boundary).
3. **Envelope** — the region of driver-space where each tier holds. Initially a hypothesis; filled in from
   evidence (existing flights are cheap samples; deliberate boundary-probing flights are the expensive
   ones, run only where a boundary matters).

Same disciplines as the sibling ledgers: **a single run rarely settles a boundary** (collect samples,
resist a clean line through two points), and **anchor to the real system** (a boundary found only in SITL
or synthetic data is a hypothesis about the vehicle, not a measurement of it). Evidence is cited by ID
into the existing ledgers (`E#` in `vio-quality-experiments.md`) rather than duplicated.

---

## Capability: GNSS / RTK positioning

**Tiers** (ArduPilot `GPS.Status`, best → worst; the boundary metric is the status field itself, backed by
`GPA.HAcc`/`VAcc` for the accuracy that actually matters):

| Tier | `Status` | Rough accuracy | Boundary metric |
|------|----------|----------------|-----------------|
| RTK-fixed | 6 | ~cm | carrier-phase ambiguities fixed |
| RTK-float | 5 | ~dm | carrier-phase, ambiguities float |
| DGPS | 4 | ~sub-m | code + corrections |
| 3D | 3 | ~1–3 m | code only (`HAcc`/`VAcc` the real tell) |
| none | <3 | — | no usable fix |

**Driver axes:**

- **Sky geometry / obstruction** *[operational]* — where the aircraft is relative to buildings, the
  treeline, canopy. Note this is *distinct* from satellite geometry (DOP): on 260712 the RTK-float→3D drop
  had **steady 30–32 sats and HDop 0.48–0.54** (E19) — the satellites were fine; the *carrier phase* was
  attenuated/multipathed by foliage. So the operative sub-driver under canopy is **signal quality, not sat
  count** — and a sat-count/HDop health check is blind to it (a load-bearing gotcha for §health signals).
- **Maneuver violence** *[operational]* — tilt angle and angular rate. High tilt can occlude the mast
  antenna's sky view and stress the RTK engine's dynamics model. *(Hypothesis; not yet isolated in data.)*
- **Situational** *[fixed / operational]* — time of day, ionospheric state, weather, **foliage wetness**
  (wet canopy attenuates far more than dry). Seasonal + diurnal.
- **Corrections feed** *[controllable]* — the RTCM stream's **rate**, **latency / correction age**,
  **message set delivered**, and **baseline length** to the base. Today: base → ntrip (tiles) → mavproxy →
  house WiFi → Boxer ELRS backpack (UDP) → ELRS uplink → FC → F9P (see fables `flight-platform.md`), at
  ~333 Hz / 1:2 telemetry, ~13 kbaud budget on the ELRS link — a **thin, latency-prone** path.

**The lever worth naming:** the corrections feed is the most controllable axis. In a limited physical
space we could bypass the ELRS bottleneck and deliver RTCM **over WiFi straight to the coordinator**
(higher rate, lower latency, fuller message set), then into the FC over the fast SERIAL4 link — plausibly
holding RTK-float or fixed in conditions where the thin ELRS feed drops to 3D. That makes "corrections
path" an envelope-**widening** experiment, not just a fixed constraint.

**Open envelope questions (to fill from data):** where in the yard does each tier hold at a given time of
day/season? At what tilt/rate does RTK drop regardless of geometry? How much does the WiFi corrections
path move the canopy boundary?

---

## Capability: VIO (stereo-only, `imu:0`)

**Tiers** (from the onboard `VISP` + offline regen vs EKF; boundary metrics are the feature count and the
divergence flag):

| Tier | Behaviour | Boundary metric |
|------|-----------|-----------------|
| bounded-tracking | usable local pose, drift within budget | ATE within budget; feature count healthy (~30–44) |
| degraded | tracking but drifting / under-scaled | scale error grows; feature count falling |
| diverged | runaway (bounded ~tens of m, fail-confident) | `vio_velocity_runaway`; feature count collapsed (~≤4) |

(Failure here is the **stereo light-starvation** mode, E19 — bounded ~52 m; *not* the `imu:1` runaway,
which is a different, catastrophic ~866 km mode, #120. The IMU is out for good reason.)

**Driver axes:**

- **Scene** *[operational / geographic]* — feature **density / texture** and **available light (photons)**.
  On 260712 the divergence was a ~10× feature collapse coincident with a light collapse on woods entry
  (E19). This is the dominant driver so far.
- **Motion** *[operational]* — the honest VIO-relevant quantity is not velocity or exposure alone but
  **distance travelled during an exposure** (velocity × exposure time = motion blur *in pixels*), plus
  angular rate. Two flights at the same speed but different light (hence exposure) sit at different points
  in this space. This suggests the envelope is best drawn in a **(blur-pixels, feature-density)** plane
  rather than a raw velocity axis.
- **Exposure / gain** *[controllable]* — the mono auto-exposure shutter. Uncapped, it lengthens in low
  light and blurs the global-shutter frame; capping it (`OAK_MONO_MAX_EXPOSURE_US`, #125/#128) trades to
  gain instead. This is the direct lever on the blur axis — but it costs SNR, so it likely *moves* the
  boundary rather than removing it (bright-scene envelope vs dark-scene envelope).
- **Geography × time** *[fixed / operational]* — a given patch of woods is systematically more or less
  **shadowed** by season and time of day (sun angle, leaf-on/leaf-off). The "can VIO hold here" envelope
  is therefore partly a **calendar/clock** map of a specific site, not just a physics constant.

**Open envelope questions:** at what blur-pixels / feature-density does tracking cross bounded→degraded→
diverged? How much does the exposure cap move that boundary (needs the mono capture, #128, to measure the
input)? Which parts of the flight site hold VIO at which times of day/year?

---

## The cross-coupling (the finding that makes this framing matter)

The two capabilities above are **not independent** — and their failure envelopes **overlap in exactly the
regime the aircraft exists to handle.** On 260712 (E19), entering the woods:

- VIO diverged at ~452 s (light/feature collapse), **and**
- GPS relaxed RTK-float → 3D ~15 s later (canopy carrier-phase attenuation),

on the **same event**, with **VIO failing first**. So the notional operating concept — "GPS degrades under
canopy, so VIO covers the gap" — has a hole: the conditions that degrade GPS (dense, dark canopy) are the
same ones that starve VIO, and VIO gives out *earlier*. The envelope where **"GPS-denied AND VIO-usable"**
holds may be **narrow or, under real canopy, empty** — which is precisely the ice-hole mission's core
regime (fables `canopy-ops.md`).

This is why the flip is worth it: only by drawing both envelopes in the **same driver-space** do you see
that the fallback and the primary fail together. It reframes the roadmap from "make each better in
isolation" to **"find or engineer conditions where at least one holds"** — e.g. push the RTK corrections
path (WiFi feed) *and* the VIO light envelope (exposure cap, fly-when-lit, fly-slower) so their union
covers the mission, even if neither alone does.

---

## Method: measuring an envelope

1. **Mine existing flights first.** Every logged flight is a trajectory through driver-space with a tier
   label at each instant (GPS status + accuracy; VIO feature count + divergence). Cheap samples — extract
   them before flying anything new. (The per-flight `manifest.json` capability tags, #121, would make this
   queryable across the whole NAS.)
2. **Probe boundaries deliberately, one driver at a time.** Where a boundary matters and the data is thin,
   fly a run that varies **one** driver (e.g. a slow vs fast pass through the same shaded patch; the same
   patch at two times of day; ELRS vs WiFi corrections in the same spot) and find where the tier flips.
3. **Then push the controllable drivers** and re-measure — that is the "expand the envelope" phase, and its
   success metric is a *moved boundary*, not a better single number.

Relation to the sibling ledgers: those hold the **theories and evidence** (why a capability behaves as it
does); this doc holds the **operational envelopes** (where each level holds) built from the same evidence.
An `E#` observation typically informs both — a theory *and* an envelope sample.

## Cross-links

- `analysis/vio-quality-experiments.md` — VIO theories/evidence (E19: the 260712 feature+light+GPS
  co-degradation this doc's cross-coupling section rests on).
- `analysis/sitl-validation-experiments.md` — FC/EKF gate behaviour (what the FC *does* as a tier degrades).
- fables `Drones/rekon10/canopy-ops.md` — the ice-hole mission doctrine and error budgets these envelopes
  serve; `flight-platform.md` — the RTCM corrections path this doc proposes rerouting over WiFi.
- #124 (feature-count health signal), #125/#128 (mono capture — the instrument for the VIO light/blur
  axis), #121 (per-flight capability tags — the query layer for step 1).
