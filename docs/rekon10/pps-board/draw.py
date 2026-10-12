"""Render the protoboard layout as an SVG, component-side view.
Run after layout.py: python3 draw.py"""
import os
import layout as P
import netlist as NL

COLS, ROWS = P.COLS, 24
S = 34                      # px per 2.54mm hole pitch
MX, MY = 60, 96            # left margin; top margin leaves room for the title
RM, BM = 178, 62           # right margin carries the breakout overhang; bottom, the legend
W = MX + (len(COLS)-1)*S + RM
H = MY + (ROWS-1)*S + BM

NETCOL = {"GND":"#4a4a4a", "3V3":"#d62728", "GND (cap)":"#8c564b",
          "SQW":"#ff7f0e", "T2_FRONT":"#1f77b4", "T2_REAR":"#2ca02c",
          "PPS_COORD":"#17becf",
          "SPI_CS":"#9467bd", "SPI_MOSI":"#9467bd", "SPI_MISO":"#9467bd", "SPI_SCLK":"#9467bd"}
PENDCOL = "#b07aa1"

def px(h):
    c, r = h.split("-")
    return MX + COLS.index(c)*S, MY + (ROWS-int(r))*S


import math as _mm
def _glyph(ref, a, b):
    """Schematic symbol for a two-lead part, along the a->b lead line.
    For a diode a is the anode and b the cathode, so the bar lands at b."""
    xa, ya = px(a); xb, yb = px(b)
    L = _mm.hypot(xb - xa, yb - ya) or 1
    ux, uy = (xb - xa) / L, (yb - ya) / L
    nx, ny = -uy, ux
    mx, my = (xa + xb) / 2, (ya + yb) / 2
    def pt(al, ac):
        return (mx + ux * al + nx * ac, my + uy * al + ny * ac)
    def line(p, q, w):
        return f'<line x1="{p[0]:.1f}" y1="{p[1]:.1f}" x2="{q[0]:.1f}" y2="{q[1]:.1f}" stroke="#6b5223" stroke-width="{w}"/>'
    out = [line((xa, ya), (xb, yb), 2)]
    if ref.startswith("R"):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(-9,-5), pt(9,-5), pt(9,5), pt(-9,5)))
        out.append(f'<polygon points="{pts}" fill="#f0e6cc" stroke="#6b5223" stroke-width="1.8"/>')
    elif ref.startswith("C"):
        for s in (-1, 1):
            out.append(line(pt(s*2.4, -8), pt(s*2.4, 8), 2.8))
    elif ref.startswith("D"):
        tri = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(-5,-7), pt(-5,7), pt(5,0)))
        out.append(f'<polygon points="{tri}" fill="#d9a441" stroke="#6b5223" stroke-width="1.6"/>')
        out.append(line(pt(5,-7), pt(5,7), 2.8))
    return out, (mx, my)

def lbl(x, y, txt, fill, size=10, anchor="start", weight="normal"):
    """Text on an opaque patch -- these labels land on top of wires."""
    w = len(txt) * size * 0.62 + 5
    bx = {"start": x-2.5, "end": x-w+2.5, "middle": x-w/2}[anchor]
    return [f'<rect x="{bx:.1f}" y="{y-size+1.5:.1f}" width="{w:.1f}" height="{size+4}" fill="#fbf8f2" opacity="0.88"/>',
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{txt}</text>']


o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="ui-monospace,Menlo,monospace">']
o.append(f'<rect width="{W}" height="{H}" fill="#fbf8f2"/>')
o.append(f'<rect x="{MX-S/2}" y="{MY-S/2}" width="{(len(COLS)-1)*S+S}" height="{(ROWS-1)*S+S}" fill="#f3eee3" stroke="#d8cfbd"/>')

# labels
for i, c in enumerate(COLS):
    x = MX + i*S
    for y, dy in ((MY-S/2-8, 0), (MY+(ROWS-1)*S+S/2+18, 0)):
        o.append(f'<text x="{x}" y="{y}" font-size="13" fill="#8a7f6a" text-anchor="middle">{c}</text>')
for r in range(1, ROWS+1):
    y = MY + (ROWS-r)*S + 4
    for x in (MX-S/2-10, MX+(len(COLS)-1)*S+S/2+10):
        o.append(f'<text x="{x}" y="{y}" font-size="12" fill="#8a7f6a" text-anchor="middle">{r:02d}</text>')

# holes
for i in range(len(COLS)):
    for r in range(1, ROWS+1):
        o.append(f'<circle cx="{MX+i*S}" cy="{MY+(ROWS-r)*S}" r="2.6" fill="#cfc4ae"/>')

# bus wires
for col, net in ((P.GNDBUS,"GND"), (P.V33BUS,"3V3")):
    x = MX + COLS.index(col)*S
    o.append(f'<line x1="{x}" y1="{MY}" x2="{x}" y2="{MY+(ROWS-1)*S}" stroke="{NETCOL[net]}" stroke-width="7" stroke-linecap="round" opacity="0.85"/>')
    o.append(f'<text x="{x}" y="{MY-S/2-24}" font-size="13" font-weight="bold" fill="{NETCOL[net]}" text-anchor="middle">{net}</text>')

# free columns
for col in ("K","L"):
    x = MX + COLS.index(col)*S
    o.append(f'<rect x="{x-S/2+3}" y="{MY-S/2+3}" width="{S-6}" height="{(ROWS-1)*S+S-6}" fill="none" stroke="#c9bfa8" stroke-dasharray="4 4"/>')
o.append(f'<text x="{MX+(COLS.index("K")+0.5)*S}" y="{MY-S/2-24}" font-size="11" fill="#9b9078" text-anchor="middle">free / test</text>')

# DIP bodies
for ref,(r1,r8) in P.CHIPS.items():
    x1, y1 = px(f"D-{r1:02d}"); x2, y2 = px(f"G-{r8:02d}")
    bx, by = min(x1,x2)-S*0.18, min(y1,y2)-S*0.42
    bw, bh = abs(x2-x1)+S*0.36, abs(y2-y1)+S*0.84
    o.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="5" fill="#9a968d" opacity="0.95"/>')
    o.append(f'<circle cx="{x1}" cy="{y1}" r="5" fill="none" stroke="#2e2b26" stroke-width="1.6"/>')
    o.append(f'<text x="{bx+bw/2}" y="{by+bh/2+5}" font-size="14" font-weight="bold" fill="#1f1d19" text-anchor="middle" transform="rotate(-90 {bx+bw/2} {by+bh/2})">{ref}  SN74AHC125N</text>')

# caps
for ref, p, n in P.caps:
    g, (mx_, my_) = _glyph("C", p, n)
    o.extend(g)
    _ux, _uy = px(p)
    o += lbl(_ux+13, _uy-8, "0.1uF", "#6b5223")

# bridges: short link between adjacent holes
for net, hs in P.bridges:
    col = NETCOL.get(net, "#666")
    for a, b in zip(hs, hs[1:]):
        xa, ya = px(a); xb, yb = px(b)
        o.append(f'<line x1="{xa}" y1="{ya}" x2="{xb}" y2="{yb}" stroke="{col}" stroke-width="5.5" stroke-linecap="round" opacity="0.95"/>')

# jumpers -- runs sharing a line are bowed apart so they do not overprint
import math as _m, collections as _c
def _lane(a, b):
    """key for 'these two wires lie along the same line'"""
    ca, ra = a.split("-"); cb, rb = b.split("-")
    if ca == cb: return ("V", ca)
    if ra == rb: return ("H", ra)
    (x1,y1),(x2,y2) = px(a), px(b)
    m = (y2-y1)/(x2-x1)
    return ("D", round(m, 2), round(y1 - m*x1, 0))
lanes = _c.defaultdict(list)
for i, (net, a, b) in enumerate(P.jumpers): lanes[_lane(a, b)].append(i)
bow = {}
for members in lanes.values():
    n = len(members)
    for k, i in enumerate(members):
        # a lone run still gets a slight bow -- easier to follow one wire by eye;
        # runs sharing a line are spread far enough apart not to overprint
        bow[i] = (3.0 if i % 2 == 0 else -3.0) if n == 1 else (k - (n-1)/2) * 7.0

for i, (net, a, b) in enumerate(P.jumpers):
    col = NETCOL.get(net, "#666")
    xa, ya = px(a); xb, yb = px(b)
    dash = ' stroke-dasharray="7 4"' if net == "GND (cap)" else ""
    d = bow[i]
    if abs(d) < 0.01:
        path = f'M{xa} {ya} L{xb} {yb}'
    else:
        L = _m.hypot(xb-xa, yb-ya) or 1
        nx, ny = -(yb-ya)/L, (xb-xa)/L          # unit normal
        cx, cy = (xa+xb)/2 + nx*d*2, (ya+yb)/2 + ny*d*2
        path = f'M{xa} {ya} Q{cx} {cy} {xb} {yb}'
    o.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="2.4" opacity="0.9"{dash}/>')
    for x,y in ((xa,ya),(xb,yb)):
        o.append(f'<circle cx="{x}" cy="{y}" r="3.4" fill="{col}"/>')


# Labels go on the side clear of the chip bodies (D-G) and the breakout (P).
LABEL_SIDE = {"C": "left", "O": "left", "P": "left"}

# discrete parts, as schematic symbols
for _ref, _val, _a, _b in P.discretes:
    _g, (_mx, _my) = _glyph(_ref, _a, _b)
    o.extend(_g)
    _side = LABEL_SIDE.get(_a.split('-')[0], 'right')
    if _side == 'left':
        o += lbl(_mx-13, _my+4, f"{_ref} {_val}", "#6b5223", anchor="end")
    else:
        o += lbl(_mx+14, _my-10, f"{_ref} {_val}", "#6b5223")

# DS3234 breakout -- header in column P, board hanging off to the right
_x7, _y7 = px(P.pin[("J2",7)]); _x1, _y1 = px(P.pin[("J2",1)])
o.append(f'<rect x="{_x7-11}" y="{_y1-13}" width="{W-MX-_x7+40}" height="{_y7-_y1+26}" rx="4" fill="#3c6e71" opacity="0.18" stroke="#3c6e71" stroke-width="1.5"/>')
o.append(f'<rect x="{_x7-9}" y="{_y1-11}" width="18" height="{_y7-_y1+22}" rx="3" fill="#ffffff" stroke="#3c6e71" stroke-width="1.6"/>')
for _n in range(1, 8):
    _px, _py = px(P.pin[("J2",_n)])
    o.append(f'<rect x="{_px-4}" y="{_py-4}" width="8" height="8" fill="#3c6e71"/>')
    o.append(f'<text x="{_px+13}" y="{_py+4}" font-size="9" fill="#2a4e50">{P.JP1[_n]}</text>')
o.append(f'<text x="{_x7+46}" y="{_y1-18}" font-size="11" font-weight="bold" fill="#2a4e50">J2  DS3234 (overhangs right)</text>')

# offboard pigtail footprints
for ref, net, sig, gnd in P.pigtails:
    xs, ys = px(sig); xg, yg = px(gnd)
    bx, by = min(xs,xg)-13, min(ys,yg)-11
    bw, bh = abs(xs-xg)+26, abs(ys-yg)+22
    o.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="3" fill="#ffffff" stroke="#6a5acd" stroke-width="1.8" opacity="0.92"/>')
    for x,y in ((xs,ys),(xg,yg)):
        o.append(f'<rect x="{x-4}" y="{y-4}" width="8" height="8" fill="#6a5acd"/>')
    o.append(f'<text x="{bx+bw/2}" y="{by-4}" font-size="10" font-weight="bold" fill="#4b3f9e" text-anchor="middle">{ref} {net.replace("PPS_","")}</text>')

# The Coordinator's wires: individual dupont legs, each landing on its own hole.
# Drawn as a tag at the destination, because there is no block on the board.
for _n, _h in sorted(P.PI.items()):
    _x, _y = px(_h)
    o.append(f'<circle cx="{_x}" cy="{_y}" r="6.5" fill="none" stroke="#4b3f9e" stroke-width="2"/>')
    _left = COLS.index(_h.split("-")[0]) > len(COLS) / 2
    o += lbl(_x + (-11 if _left else 11), _y+3.5, f"Pi {P.HDR[str(_n)][3:]}", "#4b3f9e",
             size=9, anchor="end" if _left else "start", weight="bold")

# Holes held open so the n/c outputs stay usable -- an output with no way off its
# pin is not a spare.
for _h, _who in sorted(P.RESERVED.items()):
    _x, _y = px(_h)
    o.append(f'<circle cx="{_x}" cy="{_y}" r="6" fill="none" stroke="#a33" stroke-width="1.6" stroke-dasharray="3 2.5"/>')
    o += lbl(_x+10, _y-7, f"held for {_who}", "#a33", size=8)

# pending outputs -> stub toward the right
pend = {"LED_DRV":"U3.8"}
for net, rp in pend.items():
    ref, pn = rp.split("."); h = P.pin[(ref, int(pn))]
    x, y = px(h)
    side = -1 if h[0] == "D" else 1
    o.append(f'<line x1="{x}" y1="{y}" x2="{x+side*16}" y2="{y}" stroke="{PENDCOL}" stroke-width="2.2" stroke-dasharray="3 3"/>')
    anchor = "end" if side < 0 else "start"
    o.append(f'<text x="{x+side*20}" y="{y+4}" font-size="10" fill="{PENDCOL}" text-anchor="{anchor}">{net}</text>')

# N/C
for ref, pn in P.ncs:
    x, y = px(P.pin[(ref, pn)])
    o.append(f'<path d="M{x-5} {y-5} L{x+5} {y+5} M{x+5} {y-5} L{x-5} {y+5}" stroke="#a33" stroke-width="2"/>')

# legend
lx, ly = MX, H - 22
items = [("GND","GND"),("3V3","3V3"),("GND (cap)","cap return"),
         ("SQW","SQW"),("T2_FRONT","T2_FRONT"),("T2_REAR","T2_REAR"),
         ("SPI_CS","SPI x4"),("PPS_COORD","PPS_COORD")]
step = (W - 2*MX) / len(items)
for i,(k,lab) in enumerate(items):
    x = lx + i*step
    o.append(f'<line x1="{x}" y1="{ly}" x2="{x+22}" y2="{ly}" stroke="{NETCOL[k]}" stroke-width="4"/>')
    o.append(f'<text x="{x+28}" y="{ly+4}" font-size="11" fill="#5a5245">{lab}</text>')
o.append(f'<text x="{MX}" y="{MY-S/2-50}" font-size="14" font-weight="bold" fill="#3a3429">PPS buffer board -- component side</text>')
o.append(f'<text x="{W-20}" y="{MY-S/2-50}" font-size="11" fill="{PENDCOL}" text-anchor="end">dashed violet = not placed yet</text>')
o.append('</svg>')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pps-protoboard.svg")
open(OUT, "w").write("\n".join(o) + "\n")
print("wrote pps-protoboard.svg", W, "x", H)

# A PNG in /tmp for anything that cannot render SVG (phones). The SVG is the
# committed artifact; this is a convenience render, not a second copy to go stale.
try:
    import cairosvg
    import tempfile
    png = os.path.join(tempfile.gettempdir(), "pps-protoboard.png")
    cairosvg.svg2png(url=OUT, write_to=png, scale=3, background_color="white")
    print("wrote " + png)
except ImportError:
    print("cairosvg not installed; skipped the PNG")
