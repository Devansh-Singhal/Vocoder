"""UI glue: pushes runtime state into TouchDesigner DATs/CHOPs and reads
custom parameters back off the control panel COMP.

All ops are looked up relative to `/project1/gesture_engine` by default so the
module stays portable. Nothing here does tracking work -- it is presentation
and parameter plumbing only.
"""


class UIBridge:
    def __init__(self, owner_comp):
        self.owner = owner_comp

    # ---------------- op helpers
    def op(self, name):
        return self.owner.op(name)

    def par(self, name, default=None):
        p = getattr(self.owner.par, name, None)
        return default if p is None else p.eval()

    # ---------------- control panel reads
    def read_controls(self):
        return {
            "tracking_enabled": bool(self.par("Trackingenabled", True)),
            "smoothing": float(self.par("Smoothing", 0.6)),
            "sensitivity": float(self.par("Sensitivity", 1.0)),
            "deadzone": float(self.par("Deadzone", 0.008)),
            "preset": self.par("Preset", "default"),
            "midi_port": self.par("Midiport", "loopMIDI Port"),
            "midi_channel": int(self.par("Midichannel", 1)),
        }

    # ---------------- writes
    def set_status(self, tracking, midi_status, fps, latency_ms):
        dat = self.op("status_table")
        if dat is None:
            return
        dat.clear()
        dat.appendRow(["field", "value"])
        dat.appendRow(["tracking", "ON" if tracking else "OFF"])
        dat.appendRow(["midi", midi_status])
        dat.appendRow(["fps", "%.1f" % fps])
        dat.appendRow(["latency_ms", "%.1f" % latency_ms])

    def set_gesture(self, label, features):
        dat = self.op("gesture_table")
        if dat is None:
            return
        dat.clear()
        dat.appendRow(["gesture", "value"])
        dat.appendRow(["detected", label])
        if features:
            for k in sorted(features):
                if k.startswith("_"):
                    continue
                dat.appendRow([k, "%.3f" % features[k]])

    def set_cc_table(self, rows):
        dat = self.op("cc_table")
        if dat is None:
            return
        dat.clear()
        dat.appendRow(["label", "gesture", "cc", "value"])
        for r in rows:
            dat.appendRow([str(c) for c in r])

    def log_midi(self, sent, limit=40):
        dat = self.op("midi_monitor")
        if dat is None or not sent:
            return
        if dat.numRows == 0:
            dat.appendRow(["cc", "value"])
        for cc, value in sent:
            dat.appendRow([cc, value])
        while dat.numRows > limit:
            dat.deleteRow(1)

    def set_calibration(self, prompt, progress):
        dat = self.op("calibration_table")
        if dat is None:
            return
        dat.clear()
        dat.appendRow(["prompt", prompt])
        dat.appendRow(["progress", "%.0f%%" % (progress * 100)])

    def set_preset_menu(self, names):
        p = getattr(self.owner.par, "Preset", None)
        if p is None:
            return
        p.menuNames = names
        p.menuLabels = names

    def set_midi_port_menu(self, names):
        p = getattr(self.owner.par, "Midiport", None)
        if p is None or not names:
            return
        p.menuNames = names
        p.menuLabels = names

    def set_landmarks(self, lm):
        """Write landmarks to a Table DAT the overlay TOP/SOP reads."""
        dat = self.op("landmark_table")
        if dat is None:
            return
        dat.clear()
        dat.appendRow(["i", "x", "y", "z"])
        if getattr(lm, "detected", False):
            for i, (x, y, z) in enumerate(lm.points):
                dat.appendRow([i, "%.5f" % x, "%.5f" % y, "%.5f" % z])
