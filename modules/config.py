"""Central configuration + preset/calibration file IO.

Everything that is tunable lives in JSON so the performer can change behaviour
without restarting TouchDesigner.
"""

import json
import os

# ---------------------------------------------------------------- paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_DIR = os.path.join(BASE_DIR, "presets")
CALIBRATION_PATH = os.path.join(BASE_DIR, "calibration.json")

# ---------------------------------------------------------------- defaults
DEFAULTS = {
    "midi_port_name": "loopMIDI Port",
    "midi_channel": 1,          # 1-16
    "target_fps": 60,
    "max_msgs_per_frame": 24,   # anti-flood guard
    "min_send_interval_ms": 8,  # per-CC rate limit (~125 Hz max per control)
}

# Gesture keys produced by gesture_detection.compute_features()
# Gesture keys produced by compute_features(). One hand offers roughly seven
# genuinely independent axes; the rest are derived from those, so mapping two
# keys off the same axis gives you two CCs that always move together.
#
#   independent : hand_height, hand_x, palm_rotation, pinch_amount,
#                 finger_count, finger_spread, hand_distance
#   derived     : open_hand + closed_fist    <- finger_count
#                 hand_openness              <- finger_count
#                 thumb_index_distance       <- inverse of pinch_amount
#                 wrist_*                    <- almost identical to hand_*
GESTURE_KEYS = [
    "hand_height",
    "hand_x",
    "palm_rotation",
    "hand_openness",
    "pinch_amount",
    "finger_count",
    "finger_spread",
    "hand_distance",
    "open_hand",
    "closed_fist",
    "thumb_index_distance",
    "thumb_middle_distance",
    "index_tip_x",
    "index_tip_y",
    "wrist_x",
    "wrist_y",
    "palm_center_distance",
]


class Config:
    """Holds the active preset + calibration and exposes safe accessors."""

    def __init__(self):
        self.settings = dict(DEFAULTS)
        self.preset_name = "default"
        self.preset = {"name": "default", "mappings": []}
        self.calibration = default_calibration()

    # ---------------- presets
    def list_presets(self):
        if not os.path.isdir(PRESET_DIR):
            return []
        return sorted(
            os.path.splitext(f)[0] for f in os.listdir(PRESET_DIR) if f.endswith(".json")
        )

    def load_preset(self, name):
        path = os.path.join(PRESET_DIR, name + ".json")
        with open(path, "r", encoding="utf-8") as fh:
            self.preset = json.load(fh)
        self.preset_name = name
        self.settings.update(self.preset.get("settings", {}))
        return self.preset

    def save_preset(self, name=None):
        name = name or self.preset_name
        os.makedirs(PRESET_DIR, exist_ok=True)
        self.preset["name"] = name
        with open(os.path.join(PRESET_DIR, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump(self.preset, fh, indent=2)
        self.preset_name = name

    @property
    def mappings(self):
        return self.preset.get("mappings", [])

    # ---------------- calibration
    def load_calibration(self):
        if os.path.exists(CALIBRATION_PATH):
            with open(CALIBRATION_PATH, "r", encoding="utf-8") as fh:
                self.calibration = json.load(fh)
        return self.calibration

    def save_calibration(self, data=None):
        if data is not None:
            self.calibration = data
        with open(CALIBRATION_PATH, "w", encoding="utf-8") as fh:
            json.dump(self.calibration, fh, indent=2)


def default_calibration():
    """Sane starting ranges. Run `python run_mvp.py --calibrate` to fit your hand.

    Every distance here is in *hand_scale* units -- multiples of the
    wrist-to-index-knuckle length -- because that is what
    gesture_detection.compute_features() divides by. Earlier values were raw
    image distances, roughly 5x too small, which pinned pinch_amount near zero
    no matter how hard you pinched.
    """
    return {
        # thumb-index fingertip gap: ~0.15 pinched, ~1.5 wide open
        "pinch_min": 0.15,
        "pinch_max": 1.50,
        # index-to-pinky fingertip gap: ~0.3 closed, ~1.5 spread
        "spread_min": 0.30,
        "spread_max": 1.50,
        # wrist to palm centre; barely varies, kept for palm_center_distance
        "reach_min": 0.30,
        "reach_max": 0.80,
        # thumb-to-middle gap; spans further than thumb-to-index, own range
        "thumb_middle_min": 0.45,
        "thumb_middle_max": 1.70,
        # apparent hand size: how close the hand is to the camera
        "distance_min": 0.10,
        "distance_max": 0.32,
        # hand tilt in radians, 0 = upright
        "rotation_min": -1.20,
        "rotation_max": 1.20,
        # finger-raised decision boundaries
        "straight_threshold": 0.88,
        "thumb_straight_threshold": 0.82,
        "thumb_abduction_threshold": 0.60,
    }
