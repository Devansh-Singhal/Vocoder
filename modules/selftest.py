"""Offline smoke test -- runs the whole pipeline without TouchDesigner.

    cd modules && python selftest.py
"""

import math
import sys
import time

import config as config_mod
import gesture_detection
import hand_landmarks as mp_read
import midi_output
import parameter_mapping


def fake_hand(t, openness=1.0):
    """Synthesize a plausible 21-landmark hand."""
    cx, cy = 0.5 + 0.2 * math.sin(t), 0.5 + 0.2 * math.cos(t)
    pts = [(cx, cy + 0.10, 0.0)]  # wrist
    for f in range(5):
        base_a = -1.2 + f * 0.5
        for j in range(4):
            r = (0.03 + 0.025 * j) * (0.4 + 0.6 * openness)
            pts.append((cx + r * math.sin(base_a), cy - r * math.cos(base_a), 0.0))
    return mp_read.Landmarks(pts)


# ------------------------------------------------------- finger-count poses

# Rough geometry of a hand facing the camera, in normalized image coordinates
# (y grows downward). Enough to exercise extended_fingers() with real poses.
WRIST_POS = (0.50, 0.80)
KNUCKLES = [
    ((0.44, 0.60), (0.00, -1.0)),   # index
    ((0.50, 0.59), (0.00, -1.0)),   # middle
    ((0.56, 0.60), (0.08, -1.0)),   # ring
    ((0.61, 0.63), (0.18, -1.0)),   # pinky
]
THUMB_ROOT = (0.44, 0.75)
THUMB_OUT = (-1.0, -0.15)           # abducted, clear of the palm
# A lowered thumb lies *along* the palm toward the index knuckle -- it stays
# nearly straight and stays far from the wrist. That is precisely the pose the
# old distance-from-wrist test scored as "extended", inflating every count by 1.
THUMB_IN = (0.15, -0.99)
THUMB_IN_CURL = 0.15


def _finger(origin, direction, curl, seg=0.045):
    """Four points, knuckle -> tip. curl 0 = straight, 1 = folded into the palm."""
    x, y = origin
    mag = math.hypot(direction[0], direction[1])
    dx, dy = direction[0] / mag, direction[1] / mag
    pts = [(x, y, 0.0)]
    bend = curl * math.radians(75.0)
    for _ in range(3):
        x += dx * seg
        y += dy * seg
        pts.append((x, y, 0.0))
        cos_b, sin_b = math.cos(bend), math.sin(bend)
        dx, dy = dx * cos_b - dy * sin_b, dx * sin_b + dy * cos_b
    return pts


def posed_hand(thumb, index, middle, ring, pinky):
    """Build a 21-landmark hand with the given fingers raised."""
    pts = [(WRIST_POS[0], WRIST_POS[1], 0.0)]
    pts += _finger(THUMB_ROOT, THUMB_OUT if thumb else THUMB_IN,
                   0.0 if thumb else THUMB_IN_CURL, seg=0.05)
    for up, (origin, direction) in zip((index, middle, ring, pinky), KNUCKLES):
        pts += _finger(origin, direction, 0.0 if up else 0.9)
    return mp_read.Landmarks(pts)


def check_finger_counts():
    """Regression guard: one raised finger must count as one, not two."""
    cases = [
        ("fist", (0, 0, 0, 0, 0)),
        ("one finger", (0, 1, 0, 0, 0)),
        ("two fingers", (0, 1, 1, 0, 0)),
        ("three fingers", (0, 1, 1, 1, 0)),
        ("four fingers", (0, 1, 1, 1, 1)),
        ("open hand", (1, 1, 1, 1, 1)),
        ("thumbs up", (1, 0, 0, 0, 0)),
    ]
    for name, pose in cases:
        lm = posed_hand(*pose)
        got = gesture_detection.extended_fingers(lm)
        want = [bool(p) for p in pose]
        status = "ok " if got == want else "FAIL"
        print("  %s %-14s want %d  got %d" % (status, name, sum(want), sum(got)))
        assert got == want, "%s: wanted %s, got %s" % (name, want, got)


def check_labels(cal):
    """The headline label must never contradict the finger count."""
    expected = {0: "fist", 1: "1 finger", 2: "2 fingers", 3: "3 fingers",
                4: "4 fingers", 5: "open hand"}
    for pose in [(0, 0, 0, 0, 0), (0, 1, 0, 0, 0), (0, 1, 1, 0, 0),
                 (0, 1, 1, 1, 0), (0, 1, 1, 1, 1), (1, 1, 1, 1, 1),
                 (1, 0, 0, 0, 0)]:
        feats = gesture_detection.compute_features(posed_hand(*pose), cal)
        count = feats["_finger_count_raw"]
        got = gesture_detection.label(feats)
        want = expected[count]
        print("  %s count %d -> %r" % ("ok " if got == want else "FAIL", count, got))
        assert got == want, "count %d labelled %r, expected %r" % (count, got, want)


def check_pinch_range(cal):
    """A maximum-effort pinch must reach the top of the CC range.

    The shipped ranges were once raw image distances while compute_features
    worked in hand_scale units -- about 5x out, so a full pinch topped out at
    CC 68 and anything past a light pinch read as zero.
    """
    for gap, want, name in [(0.15, 0.95, "fully pinched"), (1.40, 0.10, "wide open")]:
        got = 1.0 - gesture_detection.normalize(gap, cal["pinch_min"], cal["pinch_max"])
        ok = got >= want if name == "fully pinched" else got <= want
        print("  %s %-14s gap %.2f -> pinch %.2f" % ("ok " if ok else "FAIL", name, gap, got))
        assert ok, "%s: pinch_amount %.2f, calibration range %.2f..%.2f" % (
            name, got, cal["pinch_min"], cal["pinch_max"])


def check_rotation_continuity():
    """Tilt must sweep smoothly; atan2's seam used to sit mid-range.

    The old knuckle-axis version returned +/-pi for a sideways hand, so a
    sub-pixel wobble flipped CC 22 between 0 and 127.
    """
    previous = None
    for degrees in range(-90, 91, 2):
        a = math.radians(degrees)
        pts = [(0.0, 0.0, 0.0)] * 21
        pts[0] = (0.5, 0.7, 0.0)                                   # wrist
        pts[9] = (0.5 + 0.2 * math.sin(a), 0.7 - 0.2 * math.cos(a), 0.0)  # middle MCP
        value = gesture_detection.palm_rotation(mp_read.Landmarks(pts))
        if previous is not None:
            assert abs(value - previous) < 0.15, (
                "discontinuity at %d deg: %.2f -> %.2f" % (degrees, previous, value))
        previous = value
    print("  ok  smooth across -90..+90 deg, no seam")


class _FakeOut:
    def __init__(self):
        self.msgs = []

    def send_message(self, message):
        self.msgs.append(tuple(message))


def check_midi_retry():
    """A rate-limited message must be deferred, never dropped.

    The mapping layer records a value as sent the moment it produces it, so a
    message the sender discards is never regenerated -- which for a toggle
    means losing the single frame it changes on.
    """
    midi = midi_output.MidiSender(min_interval_ms=50)
    midi._out = _FakeOut()
    midi.connected = True

    midi.send_batch([(20, 10)])
    assert midi._out.msgs[-1] == (0xB0, 20, 10), "first value never went out"

    midi.send_batch([(20, 20)])                    # inside the rate limit
    assert midi._pending.get(20) == 20, "rate-limited value was dropped, not queued"

    time.sleep(0.06)
    midi.send_batch([])                            # nothing new; queue must drain
    assert midi._out.msgs[-1] == (0xB0, 20, 20), "queued value never re-sent"
    print("  ok  deferred value survives the rate limit and is re-sent")


# Gesture keys that are not independent of one another. Mapping both halves of
# a pair means two CCs move together on every gesture, and a DAW's MIDI learn
# cannot tell which you meant.
REDUNDANT_PAIRS = [
    (("pinch_amount", "thumb_index_distance"), "the same measurement, inverted"),
    (("finger_count", "hand_openness"), "both track how far the fingers are out"),
    (("hand_x", "wrist_x"), "the palm and the wrist move together"),
    (("hand_height", "wrist_y"), "the palm and the wrist move together"),
]


def check_preset_independence(mappings):
    """Every mapped CC must have an axis of its own."""
    used = [m["gesture"] for m in mappings if m.get("enabled", True)]
    duplicates = {g for g in used if used.count(g) > 1}
    assert not duplicates, "gesture mapped to more than one CC: %s" % sorted(duplicates)
    for (a, b), why in REDUNDANT_PAIRS:
        assert not (a in used and b in used), (
            "%s and %s are both mapped, but they are %s" % (a, b, why))
    print("  ok  %d mappings, no two sharing an axis" % len(used))


def main():
    print("finger counting:")
    check_finger_counts()

    print("palm rotation:")
    check_rotation_continuity()

    print("midi retry:")
    check_midi_retry()

    cfg = config_mod.Config()
    cfg.load_calibration()
    cfg.load_preset("default")
    engine = parameter_mapping.MappingEngine(cfg.mappings)

    print("labels:")
    check_labels(cfg.calibration)

    print("pinch range:")
    check_pinch_range(cfg.calibration)

    print("preset independence:")
    check_preset_independence(cfg.mappings)

    assert len(engine.controls) == 9, "expected 9 default mappings"

    total_msgs = 0
    for i in range(120):
        lm = fake_hand(i * 0.05, openness=0.5 + 0.5 * math.sin(i * 0.1))
        feats = gesture_detection.compute_features(lm, cfg.calibration)
        assert feats is not None
        for k, v in feats.items():
            if not k.startswith("_"):
                assert 0.0 <= v <= 1.0, "%s out of range: %s" % (k, v)
        total_msgs += len(engine.update(feats))

    # static hand for 60 frames -> dead zones must silence the stream
    static = fake_hand(1.0)
    feats = gesture_detection.compute_features(static, cfg.calibration)
    for _ in range(60):
        engine.update(feats)
    tail = sum(len(engine.update(feats)) for _ in range(30))

    print("gesture label:", gesture_detection.label(feats))
    print("messages during motion:", total_msgs)
    print("messages while still (must be 0):", tail)
    assert tail == 0, "CC flooding while hand is still"
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
