"""Exponential smoothing, dead zones and change detection.

Kept dependency-free so it runs identically inside TouchDesigner's Python and
in a plain CPython test harness.
"""


class ExpSmoother:
    """One-pole exponential smoother.

    alpha = 0 -> frozen, alpha = 1 -> no smoothing (raw passthrough).
    A 'smoothing' value of 0..1 from the UI maps to alpha = 1 - smoothing.
    """

    def __init__(self, smoothing=0.5, initial=None):
        self.set_smoothing(smoothing)
        self.value = initial

    def set_smoothing(self, smoothing):
        smoothing = min(max(float(smoothing), 0.0), 0.99)
        self.alpha = 1.0 - smoothing

    def __call__(self, sample):
        return self.update(sample)

    def update(self, sample):
        if sample is None:
            return self.value
        if self.value is None:
            self.value = float(sample)
        else:
            self.value += self.alpha * (float(sample) - self.value)
        return self.value

    def reset(self, value=None):
        self.value = value


class DeadZone:
    """Latches the output until the input moves further than `threshold`.

    Prevents jitter from generating a continuous stream of MIDI messages.
    """

    def __init__(self, threshold=0.01):
        self.threshold = float(threshold)
        self.value = None

    def update(self, sample):
        if sample is None:
            return self.value
        if self.value is None or abs(sample - self.value) >= self.threshold:
            self.value = float(sample)
        return self.value


def normalize(value, lo, hi):
    """Map value from [lo, hi] into 0..1 with clamping."""
    if hi == lo:
        return 0.0
    v = (float(value) - lo) / (hi - lo)
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
