"""Which motor is which on rekon10 -- the one lookup for labels in code, charts and text.

Corners are named from above with the nose up: NE, NW, SE, SW. The front pair is NE + NW and
the rear pair SE + SW, so a forward CG shows as front against rear (coordinator#169). The
diagonals, the same-spin pairs, are NE + SW (CCW) against NW + SE (CW). A front/rear split is
not a diagonal one.

Nothing here is indexed by ESC telemetry index or FC output: those go through SERVOn_FUNCTION
first (`esc_motors`), because the outputs are remapped (output 2 is Motor3). The table and how
it was checked against flight data: docs/rekon10/ardupilot.md, "Which motor is which".
"""

# SERVOn_FUNCTION value -> ArduPilot motor
MOTOR_FUNCTION = {33: "Motor1", 34: "Motor2", 35: "Motor3", 36: "Motor4"}

# ArduPilot motor -> (corner, position, spin), from ArduCopter 4.7.2 AP_MotorsMatrix quad X
MOTORS = {
    "Motor1": ("NE", "front-right", "CCW"),
    "Motor2": ("SW", "rear-left", "CCW"),
    "Motor3": ("NW", "front-left", "CW"),
    "Motor4": ("SE", "rear-right", "CW"),
}

CORNER = {m: v[0] for m, v in MOTORS.items()}
POSITION = {m: v[1] for m, v in MOTORS.items()}
SPIN = {m: v[2] for m, v in MOTORS.items()}
FRONT = ("NE", "NW")
REAR = ("SE", "SW")
CCW_DIAGONAL = ("NE", "SW")
CW_DIAGONAL = ("NW", "SE")

# a fixed colour per corner, so every chart draws a motor the same way
COLOUR = {"NE": "#e41a1c", "NW": "#377eb8", "SE": "#4daf4a", "SW": "#984ea3"}


def esc_motors(parms: dict) -> dict:
    """ESC telemetry index (= FC output - 1) -> ArduPilot motor, from a log's PARM values."""
    out = {}
    for name, value in parms.items():
        if name.startswith("SERVO") and name.endswith("_FUNCTION") and int(value) in MOTOR_FUNCTION:
            out[int(name[5:].split("_")[0]) - 1] = MOTOR_FUNCTION[int(value)]
    return out


def label(motor: str) -> str:
    """'NW (front-left)' -- the label to put on a line, a table row or a sentence."""
    return f"{CORNER[motor]} ({POSITION[motor]})"


def esc_labels(parms: dict) -> dict:
    """ESC telemetry index -> label, for indexing ESC rows straight off `Instance`."""
    return {i: label(m) for i, m in esc_motors(parms).items()}
