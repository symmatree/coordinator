"""Build the protoboard layout, verify it against the netlist, and write it into
central-hub.md. Run from anywhere: python3 layout.py"""
import collections, os
import netlist as NL

COLS = "ABCDEFGHIKLMNOPQRS"
GNDBUS, V33BUS = "A", "M"
CHIPS = {"U1": (7, 1), "U2": (15, 9), "U3": (23, 17)}   # row of pin1 (col D), row of pin8 (col G)
AHC = {1:"1OE",2:"1A",3:"1Y",4:"2OE",5:"2A",6:"2Y",7:"GND",
       8:"3Y",9:"3A",10:"3OE",11:"4Y",12:"4A",13:"4OE",14:"VCC"}

def hole(c, r): return f"{c}-{r:02d}"
pin = {}
for ref,(r1,r8) in CHIPS.items():
    for n in range(1,8):  pin[(ref,n)] = hole("D", r1-(n-1))
    for n in range(8,15): pin[(ref,n)] = hole("G", r8+(n-8))

# DS3234 breakout (BOB-10160): 1x7 header up column P, JP1.7 (GND) at row 01, so the
# board itself overhangs to the right. JP1 order is SS,MOSI,MISO,SCLK,SQW,VCC,GND.
RTC_COL, RTC_BOT = "P", 1
JP1 = {1:"SS", 2:"MOSI", 3:"MISO", 4:"SCLK", 5:"SQW", 6:"VCC", 7:"GND"}
for n in range(1, 8):
    pin[("J2", n)] = hole(RTC_COL, RTC_BOT + (7 - n))

bridges, jumpers, caps, ncs = [], [], [], []
def B(net, *hs): bridges.append((net, list(hs)))
def J(net, a, b): jumpers.append((net, a, b))

for ref,(r1,r8) in CHIPS.items():
    # --- GND: pins 1,4 out to C; 7 out to C (bus) and E (channel, for the cap); 10,13 out to H
    for n, c in ((1,"C"), (4,"C")):
        h = hole(c, r1-(n-1)); B("GND", pin[(ref,n)], h); J("GND", h, hole(GNDBUS, r1-(n-1)))
    r7 = r1-6
    B("GND", pin[(ref,7)], hole("C", r7), hole("B", r7))
    J("GND", hole("C", r7), hole(GNDBUS, r7))
    for n in (10, 13):
        r = r8+(n-8); h = hole("H", r)
        B("GND", pin[(ref,n)], h); J("GND", h, hole(GNDBUS, r))
    # --- 3V3: pin 14 out to H (bus jumper) and I (cap leg)
    r14 = r8+6
    B("3V3", pin[(ref,14)], hole("H", r14), hole("I", r14))
    J("3V3", hole("I", r14), hole(V33BUS, r14))
    # --- decoupling cap: top leg hard against pin 14 at H, bottom leg steps out to I to
    #     clear the row below, then straight into the channel hole bridged from pin 7
    caps.append((ref, hole("H", r14), hole("I", r14-2)))
    J("GND (cap)", hole("I", r14-2), hole("B", r7))

def chain(net, nodes, head_taps=1):
    """nodes: list of (ref,pin). Last gets one tap, the rest two (head: head_taps)."""
    taps = []
    for i,(ref,p) in enumerate(nodes):
        src = pin[(ref,p)]
        col, row = src.split("-"); row = int(row)
        out = ["C","B"] if col == "D" else ["H","I"]
        need = head_taps if i == 0 else (1 if i == len(nodes)-1 else 2)
        hs = [hole(out[k], row) for k in range(need)]
        B(net, src, *hs); taps.append(hs)
    for i in range(len(nodes)-1):
        a = taps[i][-1]; b = taps[i+1][0]
        J(net, a, b)

# --- breakout: ground down to the bus, VCC across to the 3V3 bus, SQW to U1's input
B("GND", pin[("J2",7)], hole("O", 1))
J("GND", hole("O", 1), hole(GNDBUS, 1))
B("3V3", pin[("J2",6)], hole("O", 2))
J("3V3", hole("O", 2), hole(V33BUS, 2))
B("SQW", pin[("J2",5)], hole("O", 3))

# --- R1 (SQW pull-up) and C1 (DS3234 bypass), both at the breakout header.
#     SQW and VCC are adjacent header pins, so neither part can span its two nets
#     directly. R1 reaches the 3V3 bus up column N at its own lead pitch. C1 has
#     nowhere to go: GND and VCC arrive one row apart on JP1.7/JP1.6, the header
#     pins themselves are not available to it, and the only holes carrying those
#     two nets near the breakout are their escapes at O-01 and O-02. So it is
#     built at 2.54 mm with the leads bent in, tighter than its 5 mm body wants.
B("SQW", hole("O", 3), hole("N", 3))
pin[("R1",1)], pin[("R1",2)] = hole("N", 3), hole("N", 6)      # 3 holes, 7.62 mm
pin[("C1",1)], pin[("C1",2)] = hole("O", 2), hole("O", 1)       # VCC/GND escapes, 1 hole apart
B("3V3", hole("N", 6), hole(V33BUS, 6))
# --- LED chain off U3.8: R2 across row 17, LED beside it, cathode to the GND bus
B("LED_DRV", pin[("U3",8)], hole("H", 17))
pin[("R2",1)], pin[("R2",2)] = hole("H", 17), hole("K", 17)    # 2 holes, 5.08 mm
pin[("D1",2)], pin[("D1",1)] = hole("K", 17), hole("L", 17)    # anode, cathode
J("GND", hole("L", 17), hole(GNDBUS, 17))

discretes = [("R1", "2.2k", hole("N", 3), hole("N", 6)),
             ("C1", "0.1 uF", hole("O", 2), hole("O", 1)),
             ("R2", "1k", hole("H", 17), hole("K", 17)),
             ("D1", "LED", hole("K", 17), hole("L", 17))]

chain("SQW",      [("U1",5), ("U1",2), ("U1",9), ("U1",12)])
J("SQW", hole("O", 3), hole("C", 3))
chain("T2_FRONT", [("U1",3), ("U2",5), ("U2",2), ("U2",9), ("U2",12)])
chain("T2_REAR",  [("U1",6), ("U3",5), ("U3",2), ("U3",9), ("U3",12)])


# --- offboard pigtail legs: buffer pin -> 1k series -> connector ---------------
# The 1k lives on the board, in the signal line, one per leg (central-hub.md).
# It cannot go in-row: the connector's ground pin lands straight in the A bus
# hole, leaving one hole between it and the chip pin. So each leg bridges out of
# the pin, runs the resistor along the free column, and bridges across to the
# connector. The two G-side legs reach the bus by a jumper one hole past their
# ground pin, so no hole carries two inserted leads.
#          net        ref   R    pin        tap  conn   sig  gnd
CONN = [("PPS_FC","J3","R3",("U2", 3), "C",  8, "B", GNDBUS),
        ("PPS_SW","J7","R7",("U3", 6), "C", 16, "B", GNDBUS),
        ("PPS_SE","J6","R6",("U3", 3), "C", 24, "B", GNDBUS),
        ("PPS_NE","J4","R4",("U2", 8), "I", 11, "K", "L"),
        ("PPS_NW","J5","R5",("U2",11), "I", 14, "K", "L")]
pigtails = []
EXTRA_GND = []
for net, ref, r, (chip, p_), tap, crow, sigcol, gndcol in CONN:
    src = pin[(chip,p_)]; col, row = src.split("-"); row = int(row)
    mids = ["C"] if col == "D" else ["H", "I"]
    B(net, src, *[hole(c, row) for c in mids])            # bridge out of the pin
    pin[(r,1)], pin[(r,2)] = hole(tap, row), hole(tap, crow)
    discretes.append((r, "1k", pin[(r,1)], pin[(r,2)]))
    sig, gnd = hole(sigcol, crow), hole(gndcol, crow)
    B(net + "_J", pin[(r,2)], sig)                        # resistor far end -> connector
    if gndcol != GNDBUS:                                  # G side: one hole over, then the bus
        B("GND", gnd, hole(gndcol, crow+1)); J("GND", hole(gndcol, crow+1), hole(GNDBUS, crow+1))
        EXTRA_GND += [gnd, hole(gndcol, crow+1)]
    pigtails.append((ref, net, sig, gnd))

# --- The Coordinator's leg is not a block on this board: each wire is its own
# dupont jumper off the Pi's header, soldered at the far end straight onto the
# hole it belongs to. So J1's "placement" is a list of destinations, and the only
# board-side work it needs is getting the breakout's seven pins off column P --
# every one of them is a header pin, so every one needs the hole beside it in O.
# That is why R1 had to move to N: O-01 through O-07 are all spoken for.
for _net, _jp in (("SPI_SCLK",4), ("SPI_MISO",3), ("SPI_MOSI",2), ("SPI_CS",1)):
    B(_net, pin[("J2",_jp)], hole("O", int(pin[("J2",_jp)].split("-")[1])))
B("PPS_COORD", pin[("U3",11)], hole("H", 20))
PI = {1: hole(V33BUS,10), 2: hole("H",20), 3: hole("O",6), 4: hole(GNDBUS,13),
      5: hole("O",5), 7: hole("O",4), 8: hole("O",7)}            # 6 (GPIO25) unused
for _n, _h in PI.items(): pin[("J1",_n)] = _h

# A spare output is only spare if the hole it would escape through is still there.
RESERVED = {hole("C",10): "U2.6", hole("H",1): "U1.8", hole("H",4): "U1.11"}

for ref,p in (("U1",8), ("U1",11), ("U2",6)): ncs.append((ref,p))

# ---------------- verification ----------------
declared, parent = {h: 'GND' for h in EXTRA_GND}, {}
declared.update({hole('L',17): 'GND', hole('K',17): 'LED_A'})
# J1 is a header: each pin is its net's endpoint, with nothing bridged onto it
declared.update({pin[("J1", int(_p))]: ("3V3" if _net == "+3V3" else _net)
                 for _net, _cs in NL.NETS.items() for _r, _p in _cs if _r == "J1"})
def find(x):
    parent.setdefault(x,x)
    while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
    return x
def union(a,b): parent[find(a)]=find(b)

problems = []
for net, hs in bridges:
    base = net.split(" ")[0]
    for h in hs:
        if h in declared and declared[h] != base:
            problems.append(f"HOLE {h}: {declared[h]} vs {base}")
        declared[h] = base
    for h in hs[1:]: union(hs[0], h)
for ref, p, n in caps:
    if p not in declared: problems.append(f"CAP {ref} 3V3 leg {p} undeclared")
    declared.setdefault(n, "GND")
for net, a, b in jumpers:
    union(a, b)
    for h in (a, b):
        if h not in declared and h[0] not in (GNDBUS, V33BUS):
            problems.append(f"JUMPER endpoint {h} undeclared")

# the bus columns are continuous bare wire -- union every hole on each
for col in (GNDBUS, V33BUS):
    hs = [h for h in list(parent) + list(declared) + [a for _,a,b in jumpers] + [b for _,a,b in jumpers] if h.startswith(col+"-")]
    for h in hs[1:]: union(hs[0], h)
    for h in hs: declared.setdefault(h, "GND" if col == GNDBUS else "3V3")

PENDING = {  # C5 is not fitted -- see the passives table
           "C5_UNUSED",
           "T1_SPARE1","T1_SPARE2","U2_SPARE"}
# a hole shared by two parts is its own node, and is a connected net already
for h in list(declared): find(h)

want = collections.defaultdict(set)
for net, conns in NL.NETS.items():
    base = "3V3" if net == "+3V3" else net
    for ref, p in conns:
        if (ref, int(p)) in pin: want[base].add(pin[(ref, int(p))])
ncholes = {pin[(r,p)] for r,p in ncs}
for net in sorted(want):
    holes = sorted(want[net]); wired = [h for h in holes if h in parent]
    groups = {find(h) for h in wired}
    missing = [h for h in holes if h not in parent and h not in ncholes]
    if missing and net not in PENDING: problems.append(f"{net}: unwired {missing}")
    if len(groups) > 1: problems.append(f"{net}: split into {len(groups)} islands")
node_nets = collections.defaultdict(set)
for h,n in declared.items(): node_nets[find(h)].add(n)
for node, nets in node_nets.items():
    if len(nets) > 1:
        problems.append(f"SHORT {sorted(nets)} at {sorted(h for h in declared if find(h)==node)}")

for _h, _who in sorted(RESERVED.items()):
    if _h in declared: problems.append(f"RESERVED {_h} ({_who} escape) taken by {declared[_h]}")
print("PROBLEMS:" if problems else "VERIFIED: every placed net connected, no shorts, no orphans")
for p in problems: print("  -", p)

# ---------------- emit ----------------
out = []
out.append("```")
out.append("Components:")
out.append("")
for ref,(r1,r8) in CHIPS.items():
    tag = {"U1":"", "U2":" (FRONT)", "U3":" (REAR)"}[ref]
    out.append(f"{ref}{tag}: pin 1 at {pin[(ref,1)]}, pin 8 at {pin[(ref,8)]}")
out.append(f"J2 (DS3234 breakout JP1): pin 7 GND at {pin[('J2',7)]} up to pin 1 SS at {pin[('J2',1)]}")
out.append("    board overhangs right of column " + RTC_COL)
for ref,p,n in caps: out.append(f"0.1 uF ({ref}): {p} / {n}")
for ref, val, a, b in discretes: out.append(f"{ref} ({val}): {a} / {b}")
out.append("")
out.append("Offboard pigtails (2-pin, signal / ground):")
out.append("")
for ref, net, sig, gnd in pigtails:
    note = " -- ground pin lands on the GND bus" if gnd[0] == GNDBUS else ""
    out.append(f"{ref}: {sig} ({net}) / {gnd} (GND){note}")
out.append("")
out.append("Busses:")
out.append("")
out.append(f"GND: column {GNDBUS}, bare solid-core wire, rows 01-24")
out.append(f"3V3: column {V33BUS}, bare solid-core wire, rows 01-24")
out.append(f"K, L: free -- test points / changes")
out.append("")
out.append("N/C:")
out.append("")
for ref,p in ncs: out.append(f"{pin[(ref,p)]} ({ref}.{p} {AHC[p]})")
out.append("")
out.append("bridges:")
out.append("")
last = None
for net, hs in bridges:
    if last and net != last: out.append("")
    def _pname(r, p): return JP1[p] if r == "J2" else AHC[p]
    owner = next((f" ({r}.{p} {_pname(r,p)})" for (r,p),h in pin.items() if h == hs[0]), "")
    out.append(f"{hs[0]}{owner} / " + " / ".join(hs[1:]) + f" | {net}")
    last = net
out.append("")
out.append("jumpers:")
out.append("")
last = None
for net, a, b in jumpers:
    if last and net != last: out.append("")
    out.append(f"{a} / {b} | {net}")
    last = net
out.append("```")
block = "\n".join(out) + "\n"
DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "central-hub.md")
doc = open(DOC).read()
sec = doc.index("#### Protoboard layout")
nxt = doc.index("####", sec + 4)
a = doc.index("```", sec); b = doc.index("```", doc.index("\n", a)) + 3
assert a < nxt and b < nxt, "fenced block is not inside the layout section"
open(DOC, "w").write(doc[:a] + block.rstrip("\n") + doc[b:])

# ---- the by-net table, written into the same doc -----------------------------
HDR = {"1":"hdr17","2":"hdr18","3":"hdr19","4":"hdr20","5":"hdr21","6":"hdr22","7":"hdr23","8":"hdr24"}
NETORDER = ["+3V3","GND","SQW","SPI_CS","SPI_MOSI","SPI_MISO","SPI_SCLK","T2_FRONT","T2_REAR",
            "PPS_COORD","PPS_FC","PPS_FC_J","PPS_NE","PPS_NE_J","PPS_NW","PPS_NW_J",
            "PPS_SE","PPS_SE_J","PPS_SW","PPS_SW_J",
            "LED_DRV","LED_A","T1_SPARE1","T1_SPARE2","U2_SPARE"]
NETNOTE = {
  **{f"PPS_{k}_J": "connector side of the 1k; the leg past the series resistor"
     for k in ("FC","NE","NW","SE","SW")},
  "+3V3": "from Coordinator header pin 17; RTC and all buffers share this rail",
  "GND": "all OE pins tied low so every channel is enabled",
  "SQW": "open-drain; R1 parallels the breakout's own 10k. Rising edge is RC, falling is driven",
  "PPS_COORD": "returns on header pin 18 = BCM GPIO24",
  "T1_SPARE1": "first-tier spare output, live, for a future second-tier package",
  "T1_SPARE2": "first-tier spare output, live, for a future second-tier package",
  "U2_SPARE": "rear distributor spare output",
  "LED_DRV": "U3's third output to the resistor",
  "LED_A": "resistor to LED anode -- both land in K-17, so this net is one junction",
}
def _label(r, p):
    if r == "J1": return f"{r}.{p} ({HDR[p]})"
    if r == "J2": return f"{r}.{p} ({JP1[int(p)]})"
    if r == "D1": return f"D1.{p} ({'cathode' if p == '1' else 'anode'})"
    if r.startswith("U"): return f"{r}.{p} ({AHC[int(p)]})"
    return f"{r}.{p}"
assert set(NETORDER) == set(NL.NETS), set(NETORDER) ^ set(NL.NETS)
tbl = ["| net | pins | note |", "|-----|------|------|"]
for net in NETORDER:
    pins = ", ".join(f"`{_label(r,p)}`" for r, p in NL.NETS[net])
    tbl.append(f"| **{net}** | {pins} | {NETNOTE.get(net,'')} |")
doc = open(DOC).read()
h = doc.index("#### Net list")
ta = doc.index("| net | pins | note |", h)
tb = doc.index("\n\n", ta)
open(DOC, "w").write(doc[:ta] + "\n".join(tbl) + doc[tb:])
print("wrote the net table into central-hub.md")
print(f"wrote the layout block into central-hub.md ({len(bridges)} bridges, {len(jumpers)} jumpers)")
