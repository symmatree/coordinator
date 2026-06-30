"""ardupilot_log.py -- utilities for parsing ArduPilot DataFlash .bin logs.

Canonical source for parse_log() and ArduPilot message-type constants.
Used by the rekon10 flight-analysis notebook and any future analysis scripts.
"""

import pandas as pd
from pymavlink import mavutil

MODE_MAP = {
    0: 'Stabilize', 2: 'AltHold', 3: 'Auto', 4: 'Guided', 5: 'Loiter',
    6: 'RTL', 9: 'Land', 16: 'PosHold', 17: 'Brake', 18: 'Throw', 21: 'AutoTune',
}

EV_MAP = {
    10: 'armed', 11: 'disarmed', 15: 'auto_armed', 16: 'land_complete',
    18: 'land_complete_maybe', 25: 'set_home', 57: 'arm_disallowed',
}

SUBSYS_MAP = {
    2: 'radio', 3: 'compass', 5: 'radio_fs', 8: 'gps', 12: 'ins',
    24: 'ekf_var', 25: 'viso', 26: 'terrain', 27: 'nav', 30: 'failsafe',
}

GPS_STATUS = {
    0: 'no_gps', 1: 'no_fix', 2: 'fix_2d', 3: 'fix_3d',
    4: 'dgps', 5: 'rtk_float', 6: 'rtk_fixed',
}

DEFAULT_MESSAGES = frozenset({
    'VIBE', 'GPS', 'ATT', 'BARO', 'BAT', 'ESC', 'CTUN', 'MODE', 'ARM', 'EV',
    'IMU', 'MOTB', 'RCOU', 'MSG', 'ERR', 'XKF1', 'RATE', 'PARM',
})


def parse_log(path, message_types=None):
    """Parse an ArduPilot DataFlash .bin log into a dict of DataFrames.

    Args:
        path: path to .bin file (str or Path)
        message_types: iterable of message type strings to collect;
                       defaults to DEFAULT_MESSAGES

    Returns:
        F          -- dict[str, DataFrame] keyed by message type; every
                      DataFrame has a 't_s' column (seconds from log start)
        duration_s -- float, total log duration in seconds
        parms      -- dict[str, float], PARM values logged at boot
    """
    want = set(message_types) if message_types is not None else DEFAULT_MESSAGES
    rows = {t: [] for t in want}
    mlog = mavutil.mavlink_connection(str(path), robust_parsing=True)
    t_min = None
    while True:
        msg = mlog.recv_match(blocking=False)
        if msg is None:
            break
        mtype = msg.get_type()
        if mtype not in want:
            continue
        t = getattr(msg, 'TimeUS', None)
        if t is None:
            continue
        if t_min is None:
            t_min = t
        d = {'t_s': (t - t_min) / 1e6}
        for field in msg._fieldnames:
            d[field] = getattr(msg, field)
        rows[mtype].append(d)
    F = {t: pd.DataFrame(rows[t]) for t in want if rows[t]}
    duration = max(df['t_s'].max() for df in F.values()) if F else 0.0
    parms = {r['Name']: r['Value'] for _, r in F['PARM'].iterrows()} if 'PARM' in F else {}
    return F, duration, parms
