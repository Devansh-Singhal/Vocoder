"""Gesture value -> MIDI CC value pipeline.

Each mapping row from the preset JSON:
{
  "gesture": "hand_height",
  "cc": 11,
  "min": 0, "max": 127,
  "invert": false,
  "smoothing": 0.6,
  "deadzone": 0.008,
  "mode": "continuous",   # continuous | toggle | steps
  "steps": 8,             # only for mode == "steps"
  "enabled": true,
  "label": "Harmony Mix"
}
"""

from smoothing import DeadZone, ExpSmoother


class MappedControl:
    def __init__(self, row):
        self.row = dict(row)
        self.gesture = row["gesture"]
        self.cc = int(row["cc"])
        self.lo = int(row.get("min", 0))
        self.hi = int(row.get("max", 127))
        self.invert = bool(row.get("invert", False))
        self.mode = row.get("mode", "continuous")
        self.steps = int(row.get("steps", 8))
        self.enabled = bool(row.get("enabled", True))
        self.label = row.get("label", self.gesture)
        self.smoother = ExpSmoother(row.get("smoothing", 0.5))
        self.deadzone = DeadZone(row.get("deadzone", 0.008))
        self.last_sent = None

    def set_smoothing(self, value):
        self.smoother.set_smoothing(value)

    def set_deadzone(self, value):
        self.deadzone.threshold = float(value)

    def process(self, features):
        """Return an int CC value, or None if nothing should be sent."""
        if not self.enabled or features is None:
            return None
        raw = features.get(self.gesture)
        if raw is None:
            return None

        if self.mode == "toggle":
            v = 1.0 if raw > 0.5 else 0.0          # no smoothing on switches
        else:
            v = self.smoother.update(raw)
            v = self.deadzone.update(v)
        if v is None:
            return None

        if self.invert:
            v = 1.0 - v
        if self.mode == "steps" and self.steps > 1:
            v = round(v * (self.steps - 1)) / (self.steps - 1)

        cc_value = int(round(self.lo + v * (self.hi - self.lo)))
        cc_value = max(0, min(127, cc_value))

        if cc_value == self.last_sent:
            return None                             # only transmit changes
        self.last_sent = cc_value
        return cc_value

    def reset(self):
        self.smoother.reset()
        self.deadzone.value = None
        self.last_sent = None


class MappingEngine:
    def __init__(self, mappings=None):
        self.controls = []
        self.load(mappings or [])
        self.global_sensitivity = 1.0

    def load(self, mappings):
        self.controls = [MappedControl(m) for m in mappings]

    def reset(self):
        for c in self.controls:
            c.reset()

    def apply_global(self, smoothing=None, deadzone=None):
        for c in self.controls:
            if smoothing is not None:
                c.set_smoothing(smoothing)
            if deadzone is not None:
                c.set_deadzone(deadzone)

    def update(self, features):
        """Return list of (cc_number, value, label) that actually changed."""
        out = []
        for c in self.controls:
            v = c.process(features)
            if v is not None:
                out.append((c.cc, v, c.label))
        return out

    def snapshot(self):
        """(cc, value, label) for *every* mapping, changed this frame or not.

        update() deliberately reports only changes, which is right for the MIDI
        port and wrong for a monitor: a toggle would appear for the single frame
        it flips and be invisible the rest of the time.
        """
        return [(c.cc, c.last_sent, c.label) for c in self.controls]

    def current_table(self):
        """Snapshot for the TouchDesigner UI monitor."""
        return [
            (c.label, c.gesture, c.cc, c.last_sent if c.last_sent is not None else "-")
            for c in self.controls
        ]
