"""Landmark access layer.

TouchDesigner ships a MediaPipe-based `Hand Tracking` palette component
(`handtracking`) that outputs the 21 landmarks into a CHOP/DAT. We read from
that component only -- no OpenCV hand tracking anywhere in this project.

Landmark order (MediaPipe Hands):
 0 wrist
 1-4  thumb  (cmc, mcp, ip, tip)
 5-8  index  (mcp, pip, dip, tip)
 9-12 middle
13-16 ring
17-20 pinky
"""

WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_TIP = 12
RING_TIP = 16
PINKY_MCP = 17
PINKY_TIP = 20

FINGER_TIPS = [THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP]

# Each finger as its full 4-joint chain, knuckle -> tip. Curl is measured from
# the angles at the two interior joints, which is orientation-independent.
FINGER_CHAINS = [
    (1, 2, 3, 4),        # thumb:  cmc, mcp, ip,  tip
    (5, 6, 7, 8),        # index:  mcp, pip, dip, tip
    (9, 10, 11, 12),     # middle
    (13, 14, 15, 16),    # ring
    (17, 18, 19, 20),    # pinky
]

NUM_LANDMARKS = 21


class Landmarks:
    """Immutable-ish wrapper around a list of 21 (x, y, z) tuples."""

    __slots__ = ("points", "detected")

    def __init__(self, points=None):
        self.points = points or []
        self.detected = len(self.points) == NUM_LANDMARKS

    def __len__(self):
        return len(self.points)

    def __getitem__(self, i):
        return self.points[i]

    def x(self, i):
        return self.points[i][0]

    def y(self, i):
        return self.points[i][1]

    def z(self, i):
        return self.points[i][2]


def from_chop(chop, hand_index=0):
    """Read landmarks from the TD Hand Tracking CHOP.

    The palette component names its channels like:
        hand1:p0:tx, hand1:p0:ty, hand1:p0:tz, hand1:p1:tx, ...
    Only one hand is tracked (hand_index 0 -> 'hand1'), per the spec.
    """
    if chop is None or chop.numSamples == 0:
        return Landmarks()

    prefix = "hand%d" % (hand_index + 1)
    pts = []
    for i in range(NUM_LANDMARKS):
        try:
            cx = chop["%s:p%d:tx" % (prefix, i)]
            cy = chop["%s:p%d:ty" % (prefix, i)]
            cz = chop["%s:p%d:tz" % (prefix, i)]
        except Exception:
            return Landmarks()
        if cx is None or cy is None:
            return Landmarks()
        pts.append((float(cx[0]), float(cy[0]), float(cz[0]) if cz is not None else 0.0))
    return Landmarks(pts)


def from_dat(dat, hand_index=0):
    """Fallback reader: a Table DAT with rows `index x y z`."""
    if dat is None or dat.numRows < NUM_LANDMARKS:
        return Landmarks()
    start = hand_index * NUM_LANDMARKS
    pts = []
    for i in range(start, start + NUM_LANDMARKS):
        row = dat.row(i)
        if row is None:
            return Landmarks()
        pts.append((float(row[1].val), float(row[2].val), float(row[3].val)))
    return Landmarks(pts)


def is_tracking(chop, hand_index=0):
    """True when the palette component reports an active hand."""
    if chop is None:
        return False
    try:
        ch = chop["hand%d:active" % (hand_index + 1)]
        if ch is not None:
            return ch[0] > 0.5
    except Exception:
        pass
    return from_chop(chop, hand_index).detected
