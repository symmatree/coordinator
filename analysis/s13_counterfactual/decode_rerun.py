import numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
from pymavlink import mavutil
REPLAY="/home/jovyan/wt-s13/analysis/s13_counterfactual/run/logs/00000001.BIN"
ORIG="/home/jovyan/datasets/flights/rekon10/260728-sunny-baseline/1980-01-08 17-07-39.bin"
ARM,DISARM=355.0,598.0
def xkf(path,cores):
    m=mavutil.mavlink_connection(path); d={c:{'t':[],'n':[],'e':[]} for c in cores}
    while True:
        x=m.recv_match(type='XKF1',blocking=False)
        if x is None: break
        c=getattr(x,'C',0)
        if c in d: d[c]['t'].append(x.TimeUS/1e6); d[c]['n'].append(x.PN); d[c]['e'].append(x.PE)
    return {c:{k:np.array(v) for k,v in dd.items()} for c,dd in d.items()}
R=xkf(REPLAY,[100,101]); O=xkf(ORIG,[0])
g=O[0]; mg=(g['t']>=ARM)&(g['t']<=DISARM); tt=g['t'][mg]; gN,gE=g['n'][mg],g['e'][mg]
print(f"GPS truth extent: N {np.ptp(gN):.1f} E {np.ptp(gE):.1f} m")
for c in (100,101):
    v=R[c]
    vN=np.interp(tt,v['t'],v['n']); vE=np.interp(tt,v['t'],v['e'])
    dev=np.hypot(vN-gN,vE-gE)
    print(f"re-run core {c} (VIO-driven EKF) vs GPS: extent N {np.ptp(v['n']):.0f} E {np.ptp(v['e']):.0f} m | "
          f"dev median {np.median(dev):.1f} p95 {np.percentile(dev,95):.1f} MAX {dev.max():.1f} m")
# plot core 100 vs GPS + vs raw VISP
v=R[100]; vN=np.interp(tt,v['t'],v['n']); vE=np.interp(tt,v['t'],v['e'])
fig,ax=plt.subplots(1,2,figsize=(13,5.5))
ax[0].plot(gE,gN,color="#2e8b57",lw=1.6,label="GPS-driven EKF (truth)")
ax[0].plot(vE,vN,color="#d1495b",lw=1.0,alpha=.85,label="VIO-driven EKF (re-run, C=100)")
ax[0].set_xlabel("East (m)");ax[0].set_ylabel("North (m)");ax[0].set_aspect("equal","datalim")
ax[0].legend(fontsize=8);ax[0].set_title("Actual EKF driven by VIO vs GPS truth (260728, unaligned)")
dev=np.hypot(vN-gN,vE-gE)
ax[1].plot(tt,dev,color="#d1495b",lw=1);ax[1].set_xlabel("time (s)");ax[1].set_ylabel("EKF(VIO)-vs-GPS error (m)")
ax[1].set_title(f"error: median {np.median(dev):.0f} m, max {dev.max():.0f} m")
fig.tight_layout();fig.savefig("run/s13_ekf_vio_vs_gps.png",dpi=120);print("wrote run/s13_ekf_vio_vs_gps.png")
