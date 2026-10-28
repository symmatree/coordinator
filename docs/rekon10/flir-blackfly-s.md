# FLIR Blackfly S BFS-U3-244S8M-C

[Back to index](README.md)

The camera itself: what the product is, what FLIR documents, and what this unit does that the
documents do not say. How it is mounted, powered, wired and run on the vehicle -- the pod
called **blackfly** -- is in [blackfly.md](../blackfly.md).

It is unlike every other camera on the vehicle in four ways, and each one changes how it is
powered, wired or used:

- **Global shutter.** The whole frame exposes at once. One trigger edge timestamps the exposure of
  every pixel, and there is no rolling-shutter readout to interact with vibration.
- **Monochrome.** No Bayer filter.
- **Large sensor, C-mount lens.** A 1.2" sensor behind an interchangeable machine-vision lens.
- **USB3 only.** At full resolution it needs a USB3 host.

Sources are the model's own pages on Teledyne FLIR's documentation site, linked per section.
Where a number is arithmetic on a published figure rather than a published figure, it says so.

## Camera

From the [BFS-U3-244S8 spec](https://softwareservices.flir.com/BFS-U3-244S8/latest/Model/spec.html)
(firmware 2103.0.628.0):

| | |
|---|---|
| Sensor | Sony IMX540, CMOS, 1.2", global shutter (Pregius S) |
| Resolution | 5320 x 4600, 24.5 MP |
| Pixel | 2.74 um |
| Active area | 14.58 x 12.60 mm, 19.27 mm diagonal -- *arithmetic, resolution x pixel* |
| ADC | 8 / 10 / 12-bit |
| Pixel formats | Mono8, Mono10p/packed, Mono12p/packed, Mono16 |
| Exposure | 16.0 us to 30.0 s |
| Gain | 0 to 47.99 dB |
| Full-res frame rate | 15 fps Mono8, 10 Mono10p/12p, 7 Mono16 (8-bit ADC, link limit 380 MBps) |
| On-camera image buffer | 240 MB -- about 9 full Mono8 frames, *arithmetic* |
| Lens mount | C-mount |
| Body | 29 x 29 x 39 mm, 53 g |
| Operating temperature | 0-65 degC, **case** |
| Power | 8-24 V via GPIO, or 5 V via USB; 4.2 W maximum |
| Interface | USB 3.1 Micro-B, USB3 Vision 1.0. Runs at USB 2.0 speed on a USB 2.0 host |

A full frame is 24.47 MB in Mono8 and 36.7 MB in Mono12p (*arithmetic*). At 15 fps Mono8 that is
367 MB/s, which is the link limit the frame-rate table was measured at.

## Lens: Kowa LM16XC

16 mm, f/2.0-f/22, C-mount, rated for 4/3" with a **23 mm image circle**, which covers the
sensor's 19.27 mm diagonal. Minimum object distance 100 mm. Vendor listings give 160 lp/mm at the
centre and 80 lp/mm at the corners, and recommend it for pixels from 3.0 um; the sensor's pixel
Nyquist is 182 lp/mm (*arithmetic*). One listing calls it 20 MP and another 26 MP.
([Stemmer](https://www.stemmer-imaging.com/en/kowa-lm16xc),
[1stVision](https://1stvision.com/lens/spec/Kowa/LM16XC),
[Kowa overview](https://cdn.graftek.com/system/files/12848/original/Kowa_Lens_Overview.pdf))

Filter thread M40.5 x 0.5 (both listings). 45 mm diameter x 79.5 mm long (Stemmer). Focus and
iris each have a locking screw (this unit). Mass is not weighed yet; Stemmer gives only the series
range, 210-270 g.

## GPIO connector

6-pin **Hirose HR10A-7R-6PB** on the back; the mating plug is **HR10A-7P-6S(73)**. FLIR sells
pre-made [GPIO cables](https://teledynevisionsolutions.com/products/hirose-hr10-6-pin-circular-connector).
Pin table and electrical limits from the model's
[Input/Output Control](https://softwareservices.flir.com/BFS-U3-244S8/latest/40-Installation/InputOutputControl.htm)
page:

| Pin | Wire | Line | Function |
|---|---|---|---|
| 1 | Green | 3 | **VAUX** power input, *or* **GPI** non-isolated input |
| 2 | Black | 0 | **OPTOIN**, opto-isolated input |
| 3 | Red | 2 | **VOUT** 3.3 V output, *or* **GPIO3** non-isolated input/output |
| 4 | White | 1 | **OPTOOUT3**, opto-isolated output |
| 5 | Blue | -- | Opto ground |
| 6 | Brown | -- | Camera power ground |

| Line | Low | High | Delay | Other |
|---|---|---|---|---|
| VAUX (pin 1) | | 8-24 V in | | |
| GPI (pin 1) | 0-1.4 V | 2.6-3.6 V | 1 us | |
| OPTOIN (pin 2) | 0-1.4 V | 2.6-30 V | 18 us rising, 9 us falling | draws 3.5-7 mA |
| VOUT (pin 3) | | 3.05-3.35 V | | 120 mA max |
| GPIO3 in (pin 3) | 0-1.4 V | 2.6-24 V | 1 us | |
| GPIO3 out (pin 3) | | up to 24 V | | open drain, needs a pull-up; sinks 25 mA |
| OPTOOUT3 (pin 4) | | up to 24 V | 36 us rising, 18 us falling | sinks 25 mA |

Pins 1 and 3 are each one thing or the other: powering from VAUX gives up Line 3, and using
Line 2 as an input or output gives up the 3.3 V output.

## Power

The camera needs up to 4.2 W, about 840 mA at 5 V (*arithmetic*). **If both USB and VAUX are
present, it runs from VAUX.** The family GPIO page for a sibling model gives VAUX as 3 W maximum
([BFS-U3-200S6](https://softwareservices.flir.com/BFS-U3-200S6/latest/Family/GPIOConnector.htm));
the 244S8's own pages give 4.2 W for the camera overall and nothing separate for VAUX.

VAUX takes 8-24 V, and powering from it costs Line 3 (the table above). FLIR's family docs say
some hosts and long cables cannot supply enough over USB and that GPIO power is required for
those systems
([BFS-U3-200S6](https://softwareservices.flir.com/BFS-U3-200S6/latest/Family/GPIOConnector.htm)).
Which of these the vehicle uses is in [blackfly.md](../blackfly.md).

## Trigger

From [Acquisition Control](https://softwareservices.flir.com/BFS-U3-244S8/latest/Model/public/AcquisitionControl.html)
and [Digital IO Control](https://softwareservices.flir.com/BFS-U3-244S8/latest/Model/public/DigitalIOControl.html).
Triggering is off by default.

| Feature | Values |
|---|---|
| `TriggerSelector` | `FrameStart`, `AcquisitionStart`, `FrameBurstStart` |
| `TriggerSource` | `Line0`-`Line3`, `Software`, user outputs, counters, logic blocks, `Action0` |
| `TriggerActivation` | `RisingEdge`, `FallingEdge`, `AnyEdge`, `LevelHigh`, `LevelLow` |
| `TriggerOverlap` | `Off` -- a trigger during readout is disregarded. `ReadOut` -- a trigger during readout starts another image. `PreviousFrame` -- listed, not described |
| `TriggerDelay` | microseconds after the trigger, before it takes effect |
| `ExposureMode` | `Timed` -- exposure is `ExposureTime`. `TriggerWidth` -- exposure lasts as long as the trigger pulse |

Inputs have a deglitch filter followed by a debounce filter, both of which delay the edge they
accept.

**Lines out.** An output line can carry `ExposureActive` (high while the sensor is exposing),
`FrameTriggerWait` (ready for a new frame trigger), `PPSSignal`, user outputs, counters or logic
blocks. Outputs are Line 1 (opto) or Line 2 (open drain).

**Which line takes a 3.3 V logic trigger.** Line 2 (red, pin 3) and Line 3 (green, pin 1) both
accept it with 1 us propagation delay and no stated current. OPTOIN accepts 3.3 V too but needs
3.5-7 mA from the driver and adds 9-18 us.

## Status LED

The model's own documentation has no LED page (its URL answers 404). The family page for a
sibling model ([BFS-U3-200S6](https://softwareservices.flir.com/BFS-U3-200S6/latest/Family/StatusLED.htm)):

| LED | Meaning |
|---|---|
| Off | No power, or LED inactive |
| Green, 1 / 2 / 3 blinks | USB1 / USB2 / USB3 link |
| Solid green | Acquisition started |
| Rapid flashing green | Firmware update in progress |
| Green and red alternating | General error |

The family page has no yellow. This unit showed yellow briefly at power-on, then blinking green.

## This unit

Serial **25034093** (`017DFD6D` in the USB device id), bought used. Read from SpinView on
Windows and from Aravis on blackfly, 2026-10-27:

- **Firmware 2103.0.628.0**, the version the spec page above is published for.
- **Settings as found:** `UserSetDefault = Default`, `TriggerMode = Off`, `ExposureAuto` and
  `GainAuto` `Continuous`, with `AutoExposureExposureTimeUpperLimit` 15000 us and
  `AutoExposureGainUpperLimit` 18 dB. Indoors, auto exposure sat at both limits.
- **On a USB 2.0 link** the camera reports `DeviceLinkThroughputLimit` 43,000,000 B/s and
  caps `AcquisitionFrameRate` at 1.757 Hz at full resolution in Mono8.
- **A camera left acquiring refuses `PixelFormat`.** The write fails `access-denied`. The XML
  locks it on `TLParamsLocked || PixelFormat_Lck`, and `PixelFormat_Lck` is bit 28 of register
  `0x86000`, which read `0xf8000009` (set) until `AcquisitionStop`, then `0xe8000009`. A
  process that exits without stopping acquisition leaves the camera in that state.
- **A dark reflective ND filter** was on the lens when it arrived.
