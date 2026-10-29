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

# EV.Id and ERR.Subsys, from ArduPilot libraries/AP_Logger/AP_Logger.h (enum class
# LogEvent, enum class LogErrorSubsystem). Ids not listed here are still in the log;
# a reader should show the number.
EV_MAP = {
    10: 'armed', 11: 'disarmed', 15: 'auto_armed', 17: 'land_complete_maybe',
    18: 'land_complete', 19: 'lost_gps', 25: 'set_home', 28: 'not_landed',
    54: 'motors_emergency_stopped', 55: 'motors_emergency_stop_cleared',
    56: 'motors_interlock_disabled', 57: 'motors_interlock_enabled',
    60: 'ekf_alt_reset', 61: 'land_cancelled_by_pilot', 62: 'ekf_yaw_reset',
    67: 'gps_primary_changed', 85: 'ek3_sources_set_to_primary',
    86: 'ek3_sources_set_to_secondary', 87: 'ek3_sources_set_to_tertiary',
}

SUBSYS_MAP = {
    1: 'main', 2: 'radio', 3: 'compass', 5: 'failsafe_radio', 6: 'failsafe_batt',
    8: 'failsafe_gcs', 9: 'failsafe_fence', 10: 'flight_mode', 11: 'gps',
    12: 'crash_check', 13: 'flip', 15: 'parachutes', 16: 'ekfcheck',
    17: 'failsafe_ekfinav', 18: 'baro', 19: 'cpu', 20: 'failsafe_adsb', 21: 'terrain',
    22: 'navigation', 23: 'failsafe_terrain', 24: 'ekf_primary', 25: 'thrust_loss_check',
    26: 'failsafe_sensors', 27: 'failsafe_leak', 28: 'pilot_input', 29: 'failsafe_vibe',
    30: 'internal_error', 31: 'failsafe_deadreckon',
}

GPS_STATUS = {
    0: 'no_gps', 1: 'no_fix', 2: 'fix_2d', 3: 'fix_3d',
    4: 'dgps', 5: 'rtk_float', 6: 'rtk_fixed',
}

DEFAULT_MESSAGES = frozenset({
    'VIBE', 'GPS', 'ATT', 'BARO', 'BAT', 'ESC', 'CTUN', 'MODE', 'ARM', 'EV',
    'IMU', 'MOTB', 'RCOU', 'MSG', 'ERR', 'XKF1', 'RATE', 'PARM',
    # GPS quality and the RF environment: fix status and the receiver's own
    # accuracy estimates, the F9P's interference view, link RSSI, and per-port
    # byte counters. docs/flight-data-interpretation.md has what each carries.
    'GPA', 'UBX1', 'UBX2', 'RAD', 'RSSI', 'UART',
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
