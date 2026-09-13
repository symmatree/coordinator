## Overview

Campod is a pi zero 2w with an attached camera (currently a Camera Module 3 Wide, depends on the optical configuration).

### Power

It gets power over the USB power-in connection; in flight this is a pigtail from a BEC providing 10A @ 5V to the whole payload.
In testing it may be a USB charger or the pigtail powered from a wall barrel jack.

### Connectivity

Main paths are gadget network through USB via the second USB port, and Wifi for initial bring-up and out of band comms when
the coordinator is not available. Only mentioned here to clarify the second USB port is in use for that, and that no external
comms currently go through the 40-pin.

It has a serial console available for bring-up monitoring.

### Accelerometers

Each campod has (up to) a pair of ADXL345 accelerometers. Nominally one is mounted attached to the campod's camera, and one is
mounted at the end of the same arm adjacent to the motors, giving motion for the arm as a whole, as well as a clearer view
of motor-sourced vibration at the root of it.

Sourced from [3d west](https://west3d.com/products/adxl345-accelerometer?variant=41129750888616) but the text on the page
is wrong, this is not the Adafruit unit with the level shifter; only active components is a "4B2K" regulator-looking chip
and the main chip itself. Traces are visible and go directly to the ADXL pins.

Looking down at board on the populated side, header-spaced holes, from top to bottom

* GND
* VCC
* CS
* INT1
* INT2
* SDO
* SDA
* SCL

### From PI (logical)

Serial console on 6/8/10

Connections to socket breakout board

* 17 VCC
* 18 (n/c)
* 19 MOSI
* 20 GND (GND0)
* 21 MISO
* 22 (n/c)
* 23 SCLK
* 24 CE0 (CS0)
* 25 GND (GND1)
* 26 CE1 (CS1)

Where GND0 / CS0 and GND1 / CS1 are the corresponding pins on the accelerometers 0 and 1.

#### Pi connection refinements

Interesting question (if we do the PCB for the breakout, below) is if it changes how we want to connect back to the pi. I could have a block
of headers and a 5x2 plug of some kind that either terminated in the same shape on the PCB, or turned it into a 1x10 ribbon, and then the board
would be a lot less coupled to this particular layout and pin assignment, as long as it was still reasonably low profile, I could use right-angle
headers for example.

Perhaps a better general solution in [this thread](https://forums.raspberrypi.com/viewtopic.php?t=386435). The headline, which is an option
but I'm not sure about it, suggests JST XH connectors are close enough to fit the spacing. (This might be a good solution for making generic
serial-pin cables that go from the 6/8/10 pins to my standard 4-pin layout that I use for FC connections.) But the more interesting idea
is the one using individual Dupont connections to whatever, and then rehousing them into a 40-pin dupont connector shell - so we could
have a single spec for the connections, and make up 4x of those shells-to-whatever (I think I'd want to use chunks of ribbon cable whenever I could,
unless they needed to switch to something like silicone wire or to be twisted in pairs for transit). But we could just have normal headers
on the zeros at all times, whether straight or right angle, rather than have to solder stuff or have sketchy single-connectors floating.
In the case of the breakout below, I'd still have to connect to something at the other end, but it could be a header block for the same kind
of shells, OR it could be a 8-pin or 10-pin JST of some kind I guess.

Honestly multi-pin dupont shells and a good strategy for working with them might be exactly what I'm looking for, even for things like the FT232H
where I have headers and I have my UART connector soldered to it, but something I need other versions and I don't have a great way to make them up.

### SMT socket breakout board

2x JST GH 1.25mm, 8-wire SMT. Male/female is less obvious with this spec but the side you move around has the male retention fixture, which I will call
the plug, and the other is mounted on the board, has a place for it to clip into, and I will call the socket.

One the backside of the board, U-shaped patch wires connect in order, since the connectors go the same way. (It's very tempting physically to put them face-to-face
but it makes the wiring go in a star, and also it doesn't QUITE fit without riding up on BOTH of the connector rows, whereas facing the same way lets them fit with
only overhang on the 9-16 row where we can do all soldering from below). I cut pieces of random wire I had around, but solid rather than stranded would be better
since we need to solder multiple wires to the same through-hole.

### Needs Optimised: breakout assembly process

With stranded wire, moderate success by stripping maybe 2mm at most, tinning it, then tinning a small amount of solder across the hole, then pushing the wire against the
remaining depression and hitting both with the iron. (I was trying to minimize how much it stuck up above the upper surface so I didn't want to do a normal through-hole
join). This worked for the patches and let me keep them close to the body of the pcb even though it was very soft wire.

From the Pi, I connected excess length of some very nice silicone wire I have, it's very flexible and shouldn't transmit any vibration at all. I ended up just cutting those
to length, stripping 1.5mm or less, and just tack soldering them to the topside like a flight controller.

This worked (as far as we know so far; waiting on replacement plugs before I can actually make up a test unit). However the patch cables were a ton of annoying iterations
(stripping, tinning, soldering, cutting to length, stripping and tinning the other end, soldering) and I got one wrong (patched across the GND lines which is fine but I was
planning to use the two GND from the pi separately). And perhaps worse, the joins from the pi are only solder, there's no mechanical attachment to the through hole. Which is
probably fine, but still since we HAVE a through hole I'd sooner use it.

The sockets are SMT parts, "vertical" with the plug coming in normal to the PCB (since there's no room for them to exit to the sides). The SMT pin-side
is toward the 1-8 side of the PCB, with the retention toward 9-16. Both aligned the same way, with a strip of kapton on the bottom to avoid shorting across them.
First attach the connections and then solder down the sockets, starting with the one closest to 9-16 since its traces will be partially covered by the body
of the other one.

### Breakout Assembly improvements

Proposed for next round: solid-core hookup wire for the patches. Solder in from the pi FIRST, from above on pins 1-8, from below on the GND and CS pin on the 9-16 side (which
don't need patches anyway). Second, cut the hookup wires, strip 2mm and push through into the hole from below while melting the pool with the iron. This should work better
than pushing the (stranded) pi wires in did, the pi wires will be mechanically through the hole and much more anchored, and the patch wires won't need to be tinned before use.

The REAL answer is to have a small run of these made up at JLBPCB or wherever, with the patch wires replaced by traces and the pi wires brought out to a single row of header-spaced
through-holes, and the sockets mounted so they can egress to the sides for a lower profile and less mechanical stress when unplugging. I'll wait for that until we have a
story on the PPS timing signals, which might also need some place to put a few traces and maybe a couple of passive components.

### sensors to connectors

JST GH 1.25mm "plug" (but actually it turns out that's not what I have so these are on hold for the moment)

With pins down, looking at back of connector, L to R

VCC, CS, INT1, MOSI, GND, MISO, GND, SCL

Note: We should CONSIDER whether to align with teh pinout on the PCBs for the accels. This is set up for two grounds to be twisted with the two signal lines but there's only a
single gnd on the pinout so we'd have to do SOMETHING there, and we don't currently carry INT2 though we could. Max run length is the part of the length of an arm,
