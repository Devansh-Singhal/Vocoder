"""GestureEngine -- the per-frame runtime. Attach as an Extension on the
`gesture_engine` Base COMP inside main.toe.

Wire an Execute DAT's onFrameEnd to:
    op('gesture_engine').ext.GestureEngine.OnFrame()
"""

import time

import calibration as calibration_mod
import config as config_mod
import gesture_detection
import hand_landmarks as mp_read
import midi_output
import parameter_mapping
import ui as ui_mod


class GestureEngine:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp
        self.config = config_mod.Config()
        self.config.load_calibration()
        self.ui = ui_mod.UIBridge(ownerComp)
        self.engine = parameter_mapping.MappingEngine()
        self.midi = midi_output.MidiSender()
        self.wizard = calibration_mod.CalibrationWizard(self.config)
        self._last_frame = time.monotonic()
        self._fps = 0.0
        self._latency = 0.0
        self._last_controls = {}
        self.Startup()

    # ------------------------------------------------------------ lifecycle
    def Startup(self):
        names = self.config.list_presets() or ["default"]
        self.ui.set_preset_menu(names)
        self.ui.set_midi_port_menu(midi_output.list_outputs())
        self.LoadPreset(self.config.list_presets() and names[0] or "default")
        self.ConnectMidi()

    def LoadPreset(self, name):
        try:
            self.config.load_preset(name)
        except Exception as exc:
            print("[GestureEngine] preset load failed:", exc)
            return
        self.engine.load(self.config.mappings)
        self.engine.reset()

    def SavePreset(self, name=None):
        self.config.save_preset(name)
        self.ui.set_preset_menu(self.config.list_presets())

    def ConnectMidi(self, port_name=None, channel=None):
        c = self.ui.read_controls()
        self.midi.channel = max(1, min(16, channel or c["midi_channel"])) - 1
        self.midi.open(port_name or c["midi_port"])
        return self.midi.status

    def RefreshMidiPorts(self):
        self.ui.set_midi_port_menu(midi_output.list_outputs())

    def StartCalibration(self):
        self.wizard.start()

    def CancelCalibration(self):
        self.wizard.cancel()

    def Panic(self):
        self.midi.panic()
        self.engine.reset()

    # ------------------------------------------------------------ per frame
    def OnFrame(self):
        t_start = time.monotonic()
        dt = t_start - self._last_frame
        self._last_frame = t_start
        if dt > 0:
            self._fps = self._fps * 0.9 + (1.0 / dt) * 0.1

        controls = self.ui.read_controls()
        self._apply_control_changes(controls)

        lm = mp_read.from_chop(self.ownerComp.op("hand_landmarks"))
        self.ui.set_landmarks(lm)

        if self.wizard.active:
            self.wizard.update(lm)
            self.ui.set_calibration(self.wizard.prompt, self.wizard.progress)
            if not self.wizard.active:
                self.engine.reset()
        else:
            self.ui.set_calibration("Idle", 0.0)

        features = None
        if controls["tracking_enabled"] and not self.wizard.active:
            features = gesture_detection.compute_features(lm, self.config.calibration)
            features = self._apply_sensitivity(features, controls["sensitivity"])

        changed = self.engine.update(features) if features else []
        sent = self.midi.send_batch(changed)

        self.ui.set_gesture(gesture_detection.label(features), features)
        self.ui.set_cc_table(self.engine.current_table())
        self.ui.log_midi(sent)
        self._latency = (time.monotonic() - t_start) * 1000.0
        self.ui.set_status(
            controls["tracking_enabled"] and getattr(lm, "detected", False),
            self.midi.status, self._fps, self._latency,
        )

    # ------------------------------------------------------------ helpers
    def _apply_control_changes(self, c):
        prev = self._last_controls
        if c.get("preset") != prev.get("preset") and c.get("preset"):
            self.LoadPreset(c["preset"])
        if (c["smoothing"], c["deadzone"]) != (prev.get("smoothing"), prev.get("deadzone")):
            self.engine.apply_global(smoothing=c["smoothing"], deadzone=c["deadzone"])
        if (c["midi_port"], c["midi_channel"]) != (prev.get("midi_port"), prev.get("midi_channel")):
            self.ConnectMidi(c["midi_port"], c["midi_channel"])
        self._last_controls = c

    @staticmethod
    def _apply_sensitivity(features, sensitivity):
        """Expand/contract each 0..1 value around 0.5 by the sensitivity gain."""
        if features is None or abs(sensitivity - 1.0) < 1e-3:
            return features
        out = {}
        for k, v in features.items():
            if k.startswith("_") or not isinstance(v, float):
                out[k] = v
            else:
                s = 0.5 + (v - 0.5) * sensitivity
                out[k] = 0.0 if s < 0.0 else (1.0 if s > 1.0 else s)
        return out
