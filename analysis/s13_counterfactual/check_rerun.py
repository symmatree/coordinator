import numpy as np
from pymavlink import mavutil
REPLAY="/home/jovyan/wt-s13/analysis/s13_counterfactual/run/logs/00000001.BIN"
ORIG="/home/jovyan/datasets/flights/rekon10/260728-sunny-baseline/1980-01-08 17-07-39.bin"

def xkf(path):
    m=mavutil.mavlink_connection(path); d={}
    while True:
        x=m.recv_match(type='XKF1',blocking=False)
        if x is None: break
        c=getattr(x,'C',0); d.setdefault(c,{'t':[],'n':[],'e':[]})
        d[c]['t'].append(x.TimeUS); d[c]['n'].append(x.PN); d[c]['e'].append(x.PE)
    return {c:{k:np.array(v) for k,v in dd.items()} for c,dd in d.items()}

def inv(path):
    m=mavutil.mavlink_connection(path); s={}
    while True:
        x=m.recv_match(blocking=False)
        if x is None: break
        t=x.get_type(); s[t]=s.get(t,0)+1
    return s

print("=== XKF1 by core: replay vs original ===")
R=xkf(REPLAY); O=xkf(ORIG)
for c in sorted(set(R)|set(O)):
    rn=R.get(c,{}).get('n',np.array([])); on=O.get(c,{}).get('n',np.array([]))
    print(f"core {c}: replay n={len(rn)}  orig n={len(on)}")
    if len(rn) and len(on) and len(rn)==len(on):
        dn=np.abs(rn-on); de=np.abs(R[c]['e']-O[c]['e'])
        print(f"   |dPN| max {dn.max():.6f}  |dPE| max {de.max():.6f}  (0=bit-identical passthrough; ~1e-6=rerun-tracked-GPS)")
        print(f"   replay PN range [{rn.min():.1f},{rn.max():.1f}]  orig PN [{on.min():.1f},{on.max():.1f}]")
print("\n=== replay log EKF/source message inventory ===")
s=inv(REPLAY)
for k in sorted(s):
    if any(k.startswith(p) for p in ('XKF','NKF','XKQ','XKV','GPS','REP','REV','RVO','ORGN','RASI','RISH')):
        print(f"  {k}: {s[k]}")
