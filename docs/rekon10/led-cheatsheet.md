# LED cheat sheet (radio, FC, GPS)

[Back to index](README.md)

What the vehicle's indicator LEDs mean, for reading its state when there is **no data
link** -- no USB host, no serial to the coordinator, no GCS. In that situation these are
the whole instrumentation surface.

Everything below is cited. Where the behaviour comes from firmware it is read from the
version this vehicle runs; where a vendor does not document something, that is said rather
than guessed.

---

## ELRS receiver -- Matek R24-TD

RGB LED, ExpressLRS **3.6.3**. All of these come from the RX-side state machine.[^elrs-rgb]

| Pattern | State |
|---------|-------|
| Hue sweep with fading brightness | Booting[^elrs-startup] |
| **Orange, even 500 ms on / 500 ms off** | **Disconnected -- searching for the handset** |
| Solid colour | **Connected.** Hue encodes the packet rate, brightness encodes TX power[^elrs-conn] |
| Solid, but dim | **Tentative** connection -- same hue, brightness capped lower than a full link[^elrs-tent] |
| **Green-to-yellow cross-fade ("breathing")** | **WiFi / web update mode** |
| Orange, 2 fast blinks then a 1 s pause | Binding mode |
| Orange, 3 fast blinks then a 1 s pause | Connected, but **model mismatch** -- RF link is up and channel data is being withheld from the FC |
| Red, fast 100 ms blink | Radio chip not found |
| Orange, 1 blink per second | No CRSF input (TX-side state) |
| Dark | Bootloader, or unpowered |

Two distinctions worth having:

- **Orange blink vs orange burst.** Disconnected is an even 1 Hz square; binding and model
  mismatch are short bursts followed by a long pause. Same colour, different meaning.
- **Green breathing means the radio has given up and gone to WiFi.** It only happens if the
  RX has *never* linked since boot, and once there, a handset power-cycle will not recover
  it -- the receiver has to be power-cycled.[^elrs-wifi]

---

## Flight controller -- TBS Lucid H7

Two board LEDs, both driven by ArduPilot (**Copter 4.7.0**): **PE4 green = GPIO(91)** is
ArduPilot's "A" LED, **PE3 blue = GPIO(90)** is its "B" LED.[^hwdef] `NTF_LED_TYPES` on
this vehicle is **33025**, which sets bit 0 (Built-in LED) among others, so these are
live.[^ntf]

Because ArduPilot drives them, **a pattern means the firmware booted**. Lit-but-static or
dark is a different fact from blinking-in-sequence.

### Green (A) -- status and arming[^ap-led2]

| Pattern | State |
|---------|-------|
| Blink at 8 Hz | Initialising |
| **Single flash, ~1 s cycle** | Disarmed, **pre-arm checks passing** |
| **Double flash per ~1 s** | Disarmed, **pre-arm checks failing** |
| Solid | Armed |
| Blink ~2 Hz | Armed, **battery failsafe** |
| Blink ~4 Hz | Armed, **radio or GCS failsafe** |
| Brief single darkening | Autotune complete |
| Brief double darkening | Autotune failed |
| Alternating with blue | Save-trim or ESC calibration |
| Short simultaneous blinks with blue | Compass or IMU temperature calibration running |

The ~1 s figure is derived: `update()` runs at 50 Hz and returns early unless
`_counter % 3 == 0`, giving 16.67 Hz; the arming counter advances on half of those ticks
through an 8-state sequence, so a full cycle is about 0.96 s.

### Blue (B) -- GPS[^ap-led2-gps]

| Pattern | State |
|---------|-------|
| **Dark** | No GPS attached, **or GPS attached with no lock** |
| Burst of blinks, then a ~1 s pause, repeating | 2D or 3D lock. **The burst length is the satellite count: blinks = sats - 6** |

**The trap:** this LED is dark for "no lock" *and* stays dark with a lock below **7
satellites**, because the burst length computes to zero or less. So a dark blue LED does
not distinguish "no GPS", "no fix", and "fix with 6 sats". The source comments the blink
rate as 2 Hz.

---

## GNSS -- Holybro H-RTK F9P Rover Lite

Three indicators, labelled on the vendor pinout drawing: an **RTK FIX** LED and a **3D
FIX** LED on the outer edge, and a **tri-coloured LED** on the face.[^holybro-pinout]

**Holybro does not document what the two discrete LEDs do.** Their product page mentions
the safety switch and the tri-coloured indicator and says nothing about blink
semantics.[^holybro-docs] So the honest position is:

- **RTK FIX and 3D FIX are board-local.** No ArduPilot parameter drives them -- ArduPilot's
  only outputs toward a GPS module are the notify RGB and the safety-switch LED -- so they
  report the receiver's own opinion regardless of what the FC thinks.
- **The tri-coloured LED is ArduPilot's notify RGB**, driven over the same I2C the compass
  is on, if a supported driver chip is fitted. Whether one is fitted on this unit is not
  established here.

### Reading the two together

A dark FC blue LED and a dark 3D FIX LED are **two independent paths agreeing on "no
fix"** -- one is ArduPilot's view over the serial link, the other is the receiver's own.
They agreeing is expected indoors and is not evidence of a fault. They *disagreeing* would
be informative: a lit 3D FIX with a dark FC blue means the module has a fix the FC is not
seeing, which points at the serial link rather than at the sky.

---

[^elrs-rgb]: ExpressLRS 3.6.3, `src/lib/LED/devRGB.cpp` -- sequence constants at lines
    283-287 (`LEDSEQ_DISCONNECTED` `{50,50}`, `LEDSEQ_BINDING` `{10,10,10,100}`,
    `LEDSEQ_MODEL_MISMATCH` `{10,10,10,10,10,100}`, `LEDSEQ_RADIO_FAILED` `{10,10}`,
    `LEDSEQ_NO_CROSSFIRE` `{10,100}`; units are 10 ms), state machine in `timeout()` at
    lines 439-505. Orange is `blinkyColor.h = 10`; the WiFi state is
    `hueFadeLED(blinkyColor, 85, 85-30, 128, 2)`, a green-to-yellow fade.
    <https://github.com/ExpressLRS/ExpressLRS/blob/3.6.3/src/lib/LED/devRGB.cpp>
[^elrs-startup]: `blinkyUpdate()` in the same file, entered while `blinkyState == STARTUP`.
[^elrs-conn]: `case connected:` -- `blinkyColor.h = ExpressLRS_currAirRate_Modparams->index * 256 / RATE_MAX`
    and `blinkyColor.v = fmap(POWERMGNT::currPower(), 0, PWR_COUNT-1, 10, 128)`.
[^elrs-tent]: `case tentative:` -- same hue expression, brightness mapped to 10-50 instead
    of 10-128, so a tentative link is visibly dimmer than a settled one.
[^elrs-wifi]: `src/lib/WIFI/devWIFI.cpp:1306` gates auto-WiFi on
    `webserverPreventAutoStart == false`, and `src/src/rx_main.cpp:905` latches that flag
    true inside `GotConnection()` -- so the timer can only fire if the receiver has not
    linked since boot.
[^hwdef]: ArduPilot Copter-4.7.0,
    `libraries/AP_HAL_ChibiOS/hwdef/TBS_LUCID_H7/hwdef.dat:93-96` --
    `PE3 LED0 OUTPUT LOW GPIO(90) # blue`, `PE4 LED1 OUTPUT LOW GPIO(91) # green`,
    `define HAL_GPIO_A_LED_PIN 91`, `define HAL_GPIO_B_LED_PIN 90`.
[^ntf]: `NTF_LED_TYPES` from [`ardupilot/rekon10-methodi.param`](../../ardupilot/README.md).
    Bit meanings: `libraries/AP_Notify/AP_Notify.cpp:185`. 33025 = bits 0 (Built-in LED),
    8 (NeoPixel) and 15 (IS31FL3195 External).
[^ap-led2]: ArduPilot Copter-4.7.0, `libraries/AP_Notify/AP_BoardLED2.cpp` -- the two-LED
    driver, `update()` from line 49. Decimation at lines 52-59; arming behaviour at
    lines 170-238; calibration and autotune cases at lines 90-168.
    <https://github.com/ArduPilot/ardupilot/blob/Copter-4.7.0/libraries/AP_Notify/AP_BoardLED2.cpp>
[^ap-led2-gps]: Same file, the `// gps light` switch at the end of `update()`: cases 0 and
    1 write the LED off; the default case pauses while `_sat_cnt < 8` then toggles while
    `_sat_cnt < (8 + (sats-6)*2)`.
[^holybro-pinout]: Vendor pinout drawing,
    [`Drones/rekon10/attachments/holybro-rover-lite-pinout.jpg`](https://github.com/symmatree/fables/blob/main/Drones/rekon10/attachments/holybro-rover-lite-pinout.jpg)
    (fables), which labels "RTK FIX LED indicator", "3D FIX LED indicator" and
    "tri-colored LED indicator".
[^holybro-docs]: <https://docs.holybro.com/gps-and-rtk-system/f9p-h-rtk-series/standard-f9p-uart/f9p-rover-lite>
    -- states the unit "has an integrated safety switch and a tri-colored LED indicator"
    and does not describe LED states. Checked 2026-09-29.
