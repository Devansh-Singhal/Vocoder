"""MIDI CC transmission with port discovery and flood protection.

Primary backend: python-rtmidi (`pip install python-rtmidi` into TouchDesigner's
Python, or use TD's own MIDI Out DAT via the `TDMidiSender` adapter below).
Works with loopMIDI, any virtual port, and hardware devices.
"""

import time

try:
    import rtmidi
except ImportError:  # pragma: no cover - TD may not have rtmidi installed
    rtmidi = None


def list_outputs():
    """Return available MIDI output port names."""
    if rtmidi is None:
        return []
    out = rtmidi.MidiOut()
    try:
        return list(out.get_ports())
    finally:
        del out


def find_port(name_fragment):
    for i, name in enumerate(list_outputs()):
        if name_fragment.lower() in name.lower():
            return i, name
    return None, None


class MidiSender:
    """Rate-limited CC sender. Only transmits values that changed."""

    def __init__(self, port_name="loopMIDI Port", channel=1,
                 min_interval_ms=8, max_msgs_per_frame=24):
        self.port_name = port_name
        self.channel = max(1, min(16, int(channel))) - 1
        self.min_interval = min_interval_ms / 1000.0
        self.max_msgs_per_frame = max_msgs_per_frame
        self.connected = False
        self.status = "disconnected"
        self._out = None
        self._last_time = {}
        self._last_value = {}
        # Values the rate limiter deferred. Without this they were dropped
        # outright, and since the mapping layer had already recorded them as
        # sent, nothing ever retried -- fatal for a toggle, whose entire
        # message stream is the one frame it changes on.
        self._pending = {}
        self.sent_count = 0

    # ---------------- connection
    def open(self, port_name=None):
        if rtmidi is None:
            self.status = "python-rtmidi not installed"
            return False
        self.close()
        self.port_name = port_name or self.port_name
        idx, name = find_port(self.port_name)
        if idx is None:
            self.status = "port not found: %s" % self.port_name
            return False
        self._out = rtmidi.MidiOut()
        self._out.open_port(idx)
        self.connected = True
        self.status = "connected: %s" % name
        return True

    def open_virtual(self, name="HarmonyGesture"):
        """Create our own virtual port (macOS/Linux; on Windows use loopMIDI)."""
        if rtmidi is None:
            return False
        self.close()
        self._out = rtmidi.MidiOut()
        self._out.open_virtual_port(name)
        self.connected = True
        self.status = "virtual: %s" % name
        return True

    def close(self):
        if self._out is not None:
            try:
                self._out.close_port()
            except Exception:
                pass
            self._out = None
        self.connected = False
        self.status = "disconnected"

    # ---------------- sending
    def send_cc(self, cc, value, force=False):
        if not self.connected:
            return False
        cc = int(cc) & 0x7F
        value = max(0, min(127, int(value)))
        now = time.monotonic()
        if not force:
            if self._last_value.get(cc) == value:
                return False
            if now - self._last_time.get(cc, 0.0) < self.min_interval:
                self._pending[cc] = value      # retry next frame, never drop
                return False
        self._out.send_message([0xB0 | self.channel, cc, value])
        self._pending.pop(cc, None)
        self._last_time[cc] = now
        self._last_value[cc] = value
        self.sent_count += 1
        return True

    def send_batch(self, messages):
        """messages: iterable of (cc, value[, label]). Returns sent list.

        Anything held back by the rate limit or the per-frame cap stays queued
        and goes out on a later frame.
        """
        queued = dict(self._pending)
        self._pending.clear()
        for msg in messages:
            queued[int(msg[0]) & 0x7F] = msg[1]

        sent = []
        for cc, value in queued.items():
            if len(sent) >= self.max_msgs_per_frame:
                self._pending[cc] = value
                continue
            if self.send_cc(cc, value):
                sent.append((cc, value))
        return sent

    def resend(self, messages):
        """Force every value out again, ignoring change detection.

        A DAW's MIDI learn binds to a CC it sees *move*. A toggle only moves on
        the single frame its gesture flips, which is near-impossible to catch
        while arming learn -- this gives you that movement on demand.
        """
        count = 0
        for msg in messages:
            if msg[1] is not None and self.send_cc(msg[0], msg[1], force=True):
                count += 1
        return count

    def panic(self):
        """Reset every CC we've touched to a safe state."""
        self._pending.clear()
        for cc in list(self._last_value):
            self.send_cc(cc, 0, force=True)


class TDMidiSender(MidiSender):
    """Adapter that pushes through a TouchDesigner `midiout1` MIDI Out DAT.

    Use when you'd rather not install python-rtmidi in TD's interpreter.
    """

    def __init__(self, dat_op, channel=1, **kwargs):
        super().__init__(channel=channel, **kwargs)
        self._dat = dat_op
        self.connected = dat_op is not None
        self.status = "TD MIDI Out DAT"

    def open(self, port_name=None):
        self.connected = self._dat is not None
        return self.connected

    def send_cc(self, cc, value, force=False):
        if not self.connected:
            return False
        cc = int(cc) & 0x7F
        value = max(0, min(127, int(value)))
        now = time.monotonic()
        if not force:
            if self._last_value.get(cc) == value:
                return False
            if now - self._last_time.get(cc, 0.0) < self.min_interval:
                self._pending[cc] = value
                return False
        self._dat.sendControl(self.channel + 1, cc, value)
        self._pending.pop(cc, None)
        self._last_time[cc] = now
        self._last_value[cc] = value
        self.sent_count += 1
        return True
