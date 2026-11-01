"""Turn 21 raw landmarks into named, normalized (0..1) gesture features."""

import math

from hand_landmarks import (
    FINGER_CHAINS,
    FINGER_TIPS,
    INDEX_MCP,
    INDEX_TIP,
    MIDDLE_MCP,
    MIDDLE_TIP,
    PINKY_MCP,
    THUMB_TIP,
    WRIST,
)
from smoothing import normalize

# A finger counts as raised once both interior joints are near-straight.
# Straightness is (sum of the two joint angles) / 360, so 0.88 ~ 158 deg each.
# The thumb bends less in its "raised" pose, hence the looser bar. A straight
# thumb can still lie flat along the palm, so it must also sit clear of the
# index knuckle -- that gap is in hand_scale units.
#
# `--calibrate` overwrites all three from your own hand; these are the
# out-of-the-box fallbacks.
DEFAULTS = {
    "straight_threshold": 0.88,
    "thumb_straight_threshold": 0.82,
    "thumb_abduction_threshold": 0.60,
    # apparent hand size, i.e. how close the hand is to the camera
    "distance_min": 0.10,
    "distance_max": 0.32,
    # thumb-to-middle gap; a wider span than thumb-to-index, hence its own range
    "thumb_middle_min": 0.45,
    "thumb_middle_max": 1.70,
}


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _dist2d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def palm_center(lm):
    idx = (WRIST, INDEX_MCP, PINKY_MCP, 13)
    x = sum(lm[i][0] for i in idx) / len(idx)
    y = sum(lm[i][1] for i in idx) / len(idx)
    z = sum(lm[i][2] for i in idx) / len(idx)
    return (x, y, z)


def hand_scale(lm):
    """Reference length so features stay distance-invariant."""
    s = _dist2d(lm[WRIST], lm[INDEX_MCP])
    return s if s > 1e-5 else 1e-5


def palm_rotation(lm):
    """Hand tilt in radians: 0 upright, negative left, positive right.

    Taken from the wrist -> middle-knuckle axis and offset so an upright hand
    reads 0. atan2 is discontinuous at +/-pi, and the knuckle axis this
    replaced sat right on that seam whenever it pointed sideways -- a one-pixel
    wobble flipped the output between its extremes. Centring on the rest pose
    puts the seam at "fingers pointing straight down", which the wrist cannot
    reach.
    """
    dx = lm[MIDDLE_MCP][0] - lm[WRIST][0]
    dy = lm[MIDDLE_MCP][1] - lm[WRIST][1]
    angle = math.atan2(dy, dx) + math.pi / 2.0  # image y grows downward
    if angle > math.pi:
        angle -= 2.0 * math.pi
    return angle


def _angle(a, b, c):
    """Interior angle at vertex `b`, in degrees."""
    v1 = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
    v2 = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
    n1 = math.sqrt(v1[0] ** 2 + v1[1] ** 2 + v1[2] ** 2)
    n2 = math.sqrt(v2[0] ** 2 + v2[1] ** 2 + v2[2] ** 2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 180.0
    dot = (v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def finger_straightness(lm, chain):
    """0..1 for one finger: 1.0 is perfectly straight, ~0.2 fully curled."""
    a, b, c, d = chain
    return (_angle(lm[a], lm[b], lm[c]) + _angle(lm[b], lm[c], lm[d])) / 360.0


def thumb_abduction(lm):
    """Thumb tip's distance from the index knuckle, in hand_scale units."""
    return _dist(lm[THUMB_TIP], lm[INDEX_MCP]) / hand_scale(lm)


def tuned(cal, name):
    """Calibrated value if `--calibrate` has been run, else the shipped default.

    Reading through `.get` rather than indexing keeps calibration files written
    by older versions working instead of raising on a key they never had.
    """
    return (cal or {}).get(name, DEFAULTS[name])


def extended_fingers(lm, cal=None):
    """Boolean list [thumb, index, middle, ring, pinky].

    Straightness comes from joint angles, not fingertip-to-wrist distance. The
    distance test this replaces counted the thumb as extended in nearly every
    pose -- the thumb's MCP sits so close to the wrist that its tip is always
    the farther of the two -- so every finger count came out one too high.
    """
    finger_bar = tuned(cal, "straight_threshold")
    thumb_bar = tuned(cal, "thumb_straight_threshold")
    out = [
        finger_straightness(lm, chain) >= (thumb_bar if i == 0 else finger_bar)
        for i, chain in enumerate(FINGER_CHAINS)
    ]
    if out[0]:
        out[0] = thumb_abduction(lm) >= tuned(cal, "thumb_abduction_threshold")
    return out


def compute_features(lm, cal):
    """Return a dict of every gesture value, each already 0..1.

    `lm` is a mediapipe.Landmarks, `cal` a calibration dict from config.
    Returns None when no hand is detected.
    """
    if not getattr(lm, "detected", False):
        return None

    scale = hand_scale(lm)
    center = palm_center(lm)
    wrist = lm[WRIST]

    thumb_index = _dist(lm[THUMB_TIP], lm[INDEX_TIP]) / scale
    thumb_middle = _dist(lm[THUMB_TIP], lm[MIDDLE_TIP]) / scale
    spread = _dist(lm[INDEX_TIP], lm[FINGER_TIPS[-1]]) / scale
    straightness = [finger_straightness(lm, chain) for chain in FINGER_CHAINS]
    fingers = extended_fingers(lm, cal)
    count = sum(1 for f in fingers if f)

    # Mean straightness needs no calibration: joint angles are already
    # scale- and distance-invariant. A loose fist sits near 0.5, a flat hand
    # at 1.0, so that span is what gets stretched across 0..1.
    openness = normalize(sum(straightness) / 5.0, 0.5, 0.95)
    pinch = 1.0 - normalize(thumb_index, cal["pinch_min"], cal["pinch_max"])

    feats = {
        # position
        "hand_x": normalize(center[0], 0.0, 1.0),
        "hand_height": 1.0 - normalize(center[1], 0.0, 1.0),
        "wrist_x": normalize(wrist[0], 0.0, 1.0),
        "wrist_y": 1.0 - normalize(wrist[1], 0.0, 1.0),
        "index_tip_x": normalize(lm[INDEX_TIP][0], 0.0, 1.0),
        "index_tip_y": 1.0 - normalize(lm[INDEX_TIP][1], 0.0, 1.0),
        # orientation
        "palm_rotation": normalize(palm_rotation(lm), cal["rotation_min"], cal["rotation_max"]),
        # shape
        "hand_openness": openness,
        "pinch_amount": pinch,
        "thumb_index_distance": normalize(thumb_index, cal["pinch_min"], cal["pinch_max"]),
        # Thumb-to-middle is systematically longer than thumb-to-index, so it
        # gets its own calibrated range. Sharing the pinch range pinned it at
        # 127 for most of its travel and never let it reach 0.
        "thumb_middle_distance": normalize(
            thumb_middle, tuned(cal, "thumb_middle_min"), tuned(cal, "thumb_middle_max")
        ),
        # Apparent hand size: push toward the camera or pull away. Everything
        # else divides by hand_scale to cancel distance out; this exposes it
        # deliberately, as the one axis independent of pose entirely.
        "hand_distance": normalize(
            scale, tuned(cal, "distance_min"), tuned(cal, "distance_max")
        ),
        "finger_spread": normalize(spread, cal["spread_min"], cal["spread_max"]),
        "palm_center_distance": normalize(
            _dist(wrist, center) / scale, cal["reach_min"], cal["reach_max"]
        ),
        # discrete -- all derived from `count`, so they can never contradict it.
        # open_hand needs all five: four fingers with the thumb tucked is the
        # counting gesture for 4, and must not also trip the open-hand toggle.
        "finger_count": count / 5.0,
        "open_hand": 1.0 if count == 5 else 0.0,
        "closed_fist": 1.0 if count == 0 else 0.0,
    }
    feats["_finger_count_raw"] = count
    feats["_fingers"] = fingers
    # Diagnostics for `run_mvp.py --debug`; the leading underscore keeps them out
    # of the UI tables and the mapping layer.
    feats["_straightness"] = straightness
    feats["_thumb_abduction"] = thumb_abduction(lm)
    return feats


def label(feats):
    """Human-readable gesture name for the UI.

    Every branch is driven by the same finger count the debug overlay shows, so
    the headline name can never disagree with it.
    """
    if feats is None:
        return "no hand"
    count = feats["_finger_count_raw"]
    if count == 0:
        return "fist"
    if count == 5:
        return "open hand"
    if feats["pinch_amount"] > 0.8:
        return "pinch"
    return "%d finger%s" % (count, "" if count == 1 else "s")
