"""Calibration wizard state machine.

Seven poses, each sampled and reduced to a median (or, for the last, a range):

  1. open_hand    - every range's upper end, plus "finger is straight"
  2. fist         - every range's lower end, plus "finger is curled"
  3. pinch_closed - thumb and index touching
  4. pinch_open   - thumb and index as far apart as they go
  5. tilt_left    - hand rotated anticlockwise
  6. tilt_right   - hand rotated clockwise
  7. distance     - hand swept toward and away from the camera

Poses 1 and 2 are what make finger counting reliable: the straightness
thresholds land midway between your own curled and straight readings, instead
of relying on constants measured against somebody else's hand.

Drive it by calling `update(landmarks)` once per frame while `active`.
"""

import time

from gesture_detection import (
    finger_straightness,
    hand_scale,
    palm_rotation,
    thumb_abduction,
)
from hand_landmarks import (
    FINGER_CHAINS,
    FINGER_TIPS,
    INDEX_TIP,
    MIDDLE_TIP,
    THUMB_TIP,
)

STEPS = [
    ("open_hand", "Open your hand wide, palm to camera, fingers spread", 2.5),
    ("fist", "Close a relaxed fist", 2.5),
    ("pinch_closed", "Touch your thumb and index fingertips together", 2.5),
    ("pinch_open", "Open thumb and index as wide as they go", 2.5),
    ("tilt_left", "Hold your hand flat and tilt it to the left", 2.5),
    ("tilt_right", "Now tilt it to the right", 2.5),
    # This one wants a range rather than a held pose, so it runs longer and is
    # reduced with percentiles instead of a median.
    ("distance", "Move your hand slowly toward the camera, then far away", 6.0),
]

# Below this many samples a step is judged too rushed and repeats.
MIN_SAMPLES = 12


def _median(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _percentile(vals, fraction):
    """Range endpoint that ignores the odd tracking glitch, unlike min/max."""
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[max(0, min(len(s) - 1, int(round(fraction * (len(s) - 1)))))]


def _dist(a, b):
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


class CalibrationWizard:
    def __init__(self, config, duration=None):
        self.config = config
        self.duration = duration        # None = use each step's own length
        self.index = -1
        self.active = False
        self.samples = []
        self.results = {}
        self._t0 = 0.0

    # ---------------- control
    def start(self):
        self.index = 0
        self.active = True
        self.results = {}
        self._begin_step()

    def cancel(self):
        self.active = False
        self.index = -1

    def _begin_step(self):
        self.samples = []
        self._t0 = time.monotonic()

    @property
    def step_name(self):
        return STEPS[self.index][0] if 0 <= self.index < len(STEPS) else "done"

    @property
    def prompt(self):
        if not self.active:
            return "Idle"
        return "%d/%d  %s" % (self.index + 1, len(STEPS), STEPS[self.index][1])

    @property
    def step_duration(self):
        return self.duration or STEPS[self.index][2]

    @property
    def progress(self):
        if not self.active:
            return 0.0
        return min(1.0, (time.monotonic() - self._t0) / self.step_duration)

    # ---------------- per-frame
    def update(self, lm):
        """Feed raw landmarks each frame while active."""
        if not self.active:
            return
        if not getattr(lm, "detected", False):
            # Don't let the clock run out while the hand is out of frame.
            self._t0 += 1.0 / 60.0
            return
        scale = hand_scale(lm)
        self.samples.append({
            "straightness": [finger_straightness(lm, c) for c in FINGER_CHAINS],
            "thumb_gap": thumb_abduction(lm),
            "pinch": _dist(lm[THUMB_TIP], lm[INDEX_TIP]) / scale,
            "thumb_middle": _dist(lm[THUMB_TIP], lm[MIDDLE_TIP]) / scale,
            "spread": _dist(lm[INDEX_TIP], lm[FINGER_TIPS[-1]]) / scale,
            "rot": palm_rotation(lm),
            "scale": scale,
        })
        if self.progress >= 1.0:
            self._finish_step()

    def _finish_step(self):
        if len(self.samples) < MIN_SAMPLES:
            self._begin_step()  # hand was barely visible; do it again
            return

        med = lambda key: _median([s[key] for s in self.samples])
        scales = [s["scale"] for s in self.samples]
        self.results[self.step_name] = {
            "straightness": [
                _median([s["straightness"][i] for s in self.samples]) for i in range(5)
            ],
            "thumb_gap": med("thumb_gap"),
            "pinch": med("pinch"),
            "thumb_middle": med("thumb_middle"),
            "spread": med("spread"),
            "rot": med("rot"),
            "scale_lo": _percentile(scales, 0.1),
            "scale_hi": _percentile(scales, 0.9),
        }

        self.index += 1
        if self.index >= len(STEPS):
            self.finish()
        else:
            self._begin_step()

    # ---------------- derivation
    def finish(self):
        self.active = False
        r = self.results
        cal = dict(self.config.calibration)
        openh, fist = r["open_hand"], r["fist"]

        # Decision boundaries sit midway between your curled and straight
        # readings. Fingers 1-4 share one bar; the thumb gets its own, since it
        # never straightens as far as the others.
        finger_pairs = [(openh["straightness"][i], fist["straightness"][i])
                        for i in range(1, 5)]
        cal["straight_threshold"] = sum((o + f) / 2.0 for o, f in finger_pairs) / 4.0
        cal["thumb_straight_threshold"] = (
            openh["straightness"][0] + fist["straightness"][0]) / 2.0
        cal["thumb_abduction_threshold"] = (openh["thumb_gap"] + fist["thumb_gap"]) / 2.0

        cal["pinch_min"] = r["pinch_closed"]["pinch"]
        cal["pinch_max"] = r["pinch_open"]["pinch"]
        # Thumb-to-middle spans further than thumb-to-index, so it needs its own
        # range; borrowing the pinch range clamped it for most of its travel.
        cal["thumb_middle_min"] = min(fist["thumb_middle"], r["pinch_closed"]["thumb_middle"])
        cal["thumb_middle_max"] = max(openh["thumb_middle"], r["pinch_open"]["thumb_middle"])
        cal["spread_min"] = fist["spread"]
        cal["spread_max"] = openh["spread"]
        cal["rotation_min"] = min(r["tilt_left"]["rot"], r["tilt_right"]["rot"])
        cal["rotation_max"] = max(r["tilt_left"]["rot"], r["tilt_right"]["rot"])
        cal["distance_min"] = r["distance"]["scale_lo"]
        cal["distance_max"] = r["distance"]["scale_hi"]

        # A range that collapsed means the two poses looked identical; fall back
        # to the shipped defaults rather than dividing by ~zero later.
        from config import default_calibration
        defaults = default_calibration()
        for lo, hi in (("pinch_min", "pinch_max"), ("spread_min", "spread_max"),
                       ("rotation_min", "rotation_max"),
                       ("thumb_middle_min", "thumb_middle_max"),
                       ("distance_min", "distance_max")):
            if cal[hi] - cal[lo] < 1e-3:
                cal[lo], cal[hi] = defaults[lo], defaults[hi]

        self.config.save_calibration(cal)
        return cal
