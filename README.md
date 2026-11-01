# Harmony Hand Control

Control **Antares Harmony Engine** with one hand, in real time, while you sing.
A webcam tracks your hand, gestures become MIDI CC messages, and Harmony Engine
responds — on your **vocal channel only**.

```
webcam → MediaPipe Hands → 21 landmarks → gesture features (0..1)
       → smoothing + dead zone → parameter mapping → MIDI CC (loopMIDI)
       → Harmony Engine VST on the mic track → processed vocal
```

The backing track never passes through Harmony Engine. It lives on a separate
DAW track with no plugin on it, so gestures can't touch it.

Built for live performance: 30–60 FPS tracking, sub-20 ms gesture-to-MIDI
latency, and no CC flooding.

---

## Quick start

New here? Follow **[QUICKSTART.md](QUICKSTART.md)** — it walks the whole chain
from zero in about 20 minutes.

The short version:

```bash
pip install -r requirements.txt
python run_mvp.py
```

A video window opens with a skeleton on your hand, the detected gesture name,
and live CC values. If the numbers move when your hand moves, it works.

| Flag | Purpose |
|---|---|
| `--calibrate` | fit the gesture ranges to your hand, then exit |
| `--list` | show available MIDI output ports and exit |
| `--port "loopMIDI Port"` | pick a MIDI port by substring |
| `--preset minimal_live` | load a different preset from `presets/` |
| `--camera 1` | use a different webcam |
| `--debug` | overlay per-finger detection numbers |
| `--no-window` | headless (Ctrl-C to stop) |

Keys in the window: `q` quit, `1`–`9` learn mode for that row, `r` re-send all,
`space` panic (all CC → 0). Each row in the overlay is numbered with its key.

The monitor shows the **current** value of every mapping, not only the ones
that just moved — a toggle changes for a single frame, so a changed-only
display makes it look like nothing was ever sent.

---

## Repository layout

```
run_mvp.py              standalone runner: webcam -> MIDI, no TouchDesigner needed
requirements.txt        dependencies for run_mvp.py
calibration.json        written by `--calibrate`
modules/
  hand_landmarks.py     landmark indices + readers (TD CHOP, TD DAT)
  gesture_detection.py  landmarks -> named 0..1 gesture features
  smoothing.py          exponential smoothing, dead zones, normalize()
  parameter_mapping.py  feature -> CC value; invert, scale, steps, toggle
  midi_output.py        port discovery + rate-limited CC sending
  calibration.py        seven-pose calibration wizard
  ui.py                 reads/writes TouchDesigner DATs and panel parameters
  config.py             preset + calibration JSON IO
  gesture_engine_ext.py the per-frame runtime (TouchDesigner Extension)
  selftest.py           offline smoke test of the whole pipeline
presets/
  default.json          nine mappings, CC 20-28
  minimal_live.json     three safe controls for a first gig
```

Every module is independently testable in plain CPython — nothing imports
TouchDesigner at module level. Run the pipeline offline with:

```bash
cd modules && python selftest.py
```

---

## How it works

### 1. Landmarks

MediaPipe Hands gives 21 points per hand as normalized image coordinates.
`run_mvp.py` reads them from OpenCV capture; inside TouchDesigner they come from
the **Hand Tracking** palette component's CHOP. Both produce the same
`Landmarks` object, so everything downstream is shared.

### 2. Gesture features

`gesture_detection.compute_features()` turns those points into ~16 named values,
each normalized to 0..1. Every distance is divided by `hand_scale()` (the
wrist-to-index-knuckle length) first, so features don't drift as you move closer
to or further from the camera.

| Key | What it measures |
|---|---|
| `hand_x`, `hand_height` | palm centre position (height is flipped so up = 1) |
| `wrist_x`, `wrist_y` | wrist position |
| `index_tip_x`, `index_tip_y` | index fingertip position |
| `palm_rotation` | hand tilt: 0.5 upright, 0 tilted left, 1 tilted right |
| `hand_openness` | mean finger straightness (0 = tight fist, 1 = flat hand) |
| `pinch_amount` | inverted thumb–index distance (1 = pinched) |
| `thumb_index_distance` | thumb to index tip |
| `thumb_middle_distance` | thumb to middle tip (own calibrated range) |
| `finger_spread` | index tip to pinky tip (fingers together vs splayed) |
| `hand_distance` | apparent hand size — push toward the camera or pull away |
| `palm_center_distance` | wrist to palm centre |
| `finger_count` | raised fingers / 5 |
| `open_hand` | 1 when all five fingers are raised |
| `closed_fist` | 1 when no fingers are raised |

`open_hand` and `closed_fist` are derived from the same finger count as
`finger_count`, so the headline gesture name in the video window can never
disagree with the per-finger readout under `--debug`.

### Independent axes

One hand offers about **seven** genuinely independent controls:

`hand_height` · `hand_x` · `palm_rotation` · `pinch_amount` · `finger_count`
· `finger_spread` · `hand_distance`

Everything else in the table above is derived from one of those. Map two keys
off the same axis and you get two CCs that always move together — a DAW's MIDI
learn cannot tell them apart, and in performance one gesture drives two plugin
parameters whether you wanted that or not. The worst offenders:

| Don't map both | Because |
|---|---|
| `pinch_amount` + `thumb_index_distance` | the same measurement, inverted — they always sum to 127 |
| `finger_count` + `hand_openness` | both track how far the fingers are out |
| `hand_x` + `wrist_x` (or `hand_height` + `wrist_y`) | the palm and the wrist move together |

`selftest.py` enforces this against the shipped presets.

**Finger counting** uses joint angles, not distances. Each finger is a four-point
chain (knuckle → tip); a finger counts as raised when both of its interior joints
are near-straight. The thumb additionally has to sit clear of the index knuckle,
because it can be perfectly straight while folded flat across the palm. The
fallback thresholds live at the top of `gesture_detection.py`, and `--calibrate`
overrides all three from your own hand:

```python
DEFAULTS = {
    "straight_threshold": 0.88,          # ~158 deg at each joint
    "thumb_straight_threshold": 0.82,
    "thumb_abduction_threshold": 0.60,   # thumb gap, in hand_scale units
    "distance_min": 0.10,                # apparent hand size, near/far
    "distance_max": 0.32,
    "thumb_middle_min": 0.45,
    "thumb_middle_max": 1.70,
}
```

Run `python run_mvp.py --debug` to see the live straightness value for each
finger next to its raised/not decision — tune against real numbers, not guesses.

Better still, run `python run_mvp.py --calibrate`. It walks seven poses (open
hand, fist, pinch closed, pinch open, tilt left, tilt right, and a near/far
sweep) and writes
`calibration.json`, putting each threshold midway between your *own* curled and
straight readings rather than constants measured on someone else's hand. Every
distance it records is in `hand_scale` units — multiples of the
wrist-to-index-knuckle length — which is what `compute_features()` divides by.

### 3. Smoothing and mapping

Per frame, for each mapping row in the active preset:

1. read the raw 0..1 feature,
2. exponential smoothing (`alpha = 1 - smoothing`),
3. dead-zone latch — the value only moves once it changes by ≥ the threshold,
4. optional invert; optional quantize to N steps, or a hard toggle at 0.5,
5. scale into `[min, max]`, clamp to 0–127, round to an integer,
6. **send only if it differs from the last value sent**.

`toggle` mode skips smoothing entirely, so switches stay crisp.

### 4. MIDI output

On top of the change-detection above, `midi_output.py` rate-limits each CC to
one message per `min_send_interval_ms` (8 ms default, ~125 Hz) and caps the whole
frame at `max_msgs_per_frame` (24). That combination is what keeps the port from
flooding while still feeling continuous.

---

## Teaching Harmony Engine the assignments

Harmony Engine has **no master wet/dry, no "Gender" and no global formant
control**. Harmony level is the four Voice Gain faders; formant character is
Throat Length, per voice. The mappings below use only controls the plugin
actually has.

Set **Key/Root** and **Scale** in Harmony Control first, or there is nothing to
harmonise to. These CCs drive Harmony Engine's *parameters* — they are not the
harmony notes.

In Reaper, with the runner going and `MIDI -> connected`:

1. Open the FX window on the vocal track, select Harmony Engine.
2. Click **Param** in the toolbar, find the parameter, choose **Learn**.
3. Make the gesture. Reaper captures the CC.
4. Turn **soft takeover off** so the value tracks your hand immediately.

### Learn mode

Gesturing a control into a Learn dialog is awkward, and the toggles barely move
at all. Instead:

1. In the video window, press the row's number key (`1`–`9`, shown beside it).
2. A red banner appears. That CC now sweeps 0 → 127 → 0 continuously, and
   **every other CC goes quiet**.
3. Switch to your DAW — the window no longer needs focus — and arm Learn. It
   binds immediately, because exactly one control is moving.
4. Press the same key again, or `0`, to stop. It expires by itself after 45 s
   and restores the CC to whatever your hand is actually doing.

This exists because keyboard focus is exclusive: `cv2.waitKey` only fires while
the video window is focused, and Learn only listens while the DAW is. Anything
requiring a keypress *during* Learn cannot work — hence a latched sweep you
start beforehand.

`r` re-sends all nine at once. Useful for checking the MIDI link is alive,
useless for Learn — the DAW gets nine simultaneous CCs and has to guess.

If a parameter name isn't in Reaper's list, move it in the plugin's own GUI
first so it becomes "last touched", then Learn.

Shipped in `presets/default.json` — the first three are `minimal_live`, and a
sensible order to learn them in:

| Gesture | CC | Harmony Engine target |
|---|---|---|
| `hand_height` | 20 | **Voice 1–4 Gain** — learn this one CC onto all four faders |
| `pinch_amount` | 23 | Glide → Transition Rate |
| `closed_fist` | 26 | Reaper's FX Bypass for the plugin (kill switch) |
| `hand_x` | 21 | Voice 1–4 Throat Length |
| `palm_rotation` | 22 | Spread (Harmony Control panel) |
| `finger_count` | 24 | Choir Size (6 steps) |
| `open_hand` | 25 | Choir Bypass — inverted, so an open hand brings the choir in |
| `finger_spread` | 27 | Choir → Vibrato |
| `hand_distance` | 28 | Humanize → Pitch Variation |

Throat Length is very sensitive, so CC 21 ships clamped to `min: 40, max: 90`
rather than the full sweep. Widen it in the preset once you know how far you
actually want to travel.

Each mapping row also carries a `_target` field naming its control, ignored by
the code and there so the preset explains itself.

---

## Adding a gesture mapping

Add a row to a preset JSON — no code change needed:

```json
{
  "label": "Pitch Correction Speed",
  "gesture": "finger_spread",
  "cc": 29,
  "min": 0, "max": 127,
  "invert": true,
  "smoothing": 0.7,
  "deadzone": 0.01,
  "mode": "continuous",
  "enabled": true
}
```

`gesture` must be one of the keys in the table above. `mode` is `continuous`,
`steps` (with `"steps": N`) or `toggle`. Higher `smoothing` is slower and
steadier; higher `deadzone` ignores more jitter.

To add a brand-new feature, compute it in `compute_features()` and normalize it
with `smoothing.normalize()` — the mapping layer picks it up automatically.

---

## Building the TouchDesigner patch

`run_mvp.py` is the whole signal chain already; TouchDesigner adds the panel,
live preset switching and the calibration wizard. Get the standalone runner
working first.

### Step 1 — webcam + MediaPipe
1. `Video Device In TOP` → `videodevin1`, 640×480, 60 FPS. Low resolution is the
   single biggest latency lever here.
2. Palette (`Alt+L`) → **Derivative → Tools → handTracking**, dropped into
   `/project1`. Source = `videodevin1`, Max Hands = 1, Model Complexity = 0,
   Min Detection Confidence ≈ 0.6.

### Step 2 — landmarks
The palette comp exposes channels named `hand1:p0:tx … hand1:p20:tz`. Create a
Base COMP `gesture_engine` in `/project1` containing:
- `hand_landmarks` — a **Select CHOP** pointing at the palette comp's landmark CHOP,
- `landmark_table`, `gesture_table`, `cc_table`, `midi_monitor`, `status_table`,
  `calibration_table` — empty **Table DATs**,
- for the skeleton overlay: `landmark_table` → **DAT to CHOP** → **CHOP to SOP** →
  `Geometry COMP`, over the webcam TOP.

### Step 3 — extension + per-frame call
1. Drop `modules/*.py` into `gesture_engine` as **Text DATs** (same names, no
   `.py`), or add `modules/` to the Python Module Search Path.
2. On `gesture_engine`: Extensions page → `Extension 1` =
   `op('gesture_engine_ext').module.GestureEngine(me)`, Promote on.
3. Add an **Execute DAT** with `onFrameEnd`:
   ```python
   def onFrameEnd(frame):
       op('/project1/gesture_engine').ext.GestureEngine.OnFrame()
   ```

### Step 4 — control panel
Custom parameters on `gesture_engine`:

| Parameter | Type | Purpose |
|---|---|---|
| `Trackingenabled` | Toggle | enable/disable tracking |
| `Smoothing` | Float 0–0.95 | global smoothing |
| `Sensitivity` | Float 0.5–2.0 | expand range around centre |
| `Deadzone` | Float 0–0.05 | global dead zone |
| `Preset` | Menu | filled from `presets/` |
| `Midiport` | Menu | filled from detected MIDI outputs |
| `Midichannel` | Int 1–16 | output channel |
| `Calibrate` | Pulse | `ext.GestureEngine.StartCalibration()` |
| `Refreshports` | Pulse | `ext.GestureEngine.RefreshMidiPorts()` |
| `Panic` | Pulse | `ext.GestureEngine.Panic()` |

### Step 5 — DAW routing
1. **Track 1 — Vocal**: input = your mic. Insert Harmony Engine. Monitor = In.
   Buffer 64–128 samples.
2. **Track 2 — Backing track**: audio file, **no plugins**, never bussed into
   track 1.
3. **MIDI input**: enable `loopMIDI Port` as a remote/control surface —
   Ableton: Preferences → Link/MIDI → Remote ON. Reaper: Preferences → MIDI
   Devices → enable input.

### Step 6 — calibrate
Hit `Calibrate`, follow the six prompts, then switch presets live from the
`Preset` menu. No restart needed.

---

## Requirements

- Python 3.11+ (developed against 3.12)
- **loopMIDI** on Windows, with one port created. On macOS/Linux the runner
  creates its own virtual port named `HarmonyGesture`.
- Ableton Live or Reaper, plus the Antares Harmony Engine VST.
- For the TouchDesigner path: TouchDesigner 2023.11+ (its Hand Tracking palette
  component is MediaPipe-based — this project never uses OpenCV hand tracking
  inside TD) and `python-rtmidi` installed into TouchDesigner's own Python
  (Preferences → Python 64-bit Module Path). If you'd rather not, use the
  `TDMidiSender` adapter with a MIDI Out DAT instead.

---

## Latency checklist

- Webcam 640×480 @ 60 FPS, MJPEG, model complexity 0.
- TouchDesigner realtime ON, target 60 FPS. The whole `OnFrame()` pass is
  reported live in `status_table → latency_ms`, typically 1–3 ms; the camera and
  MediaPipe inference dominate.
- Audio buffer 64–128 samples in the DAW. Audio never passes through
  TouchDesigner, so gesture latency and audio latency are independent.
- If gestures feel laggy, lower `Smoothing` before touching anything else.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not open camera 0` | try `--camera 1` |
| `MIDI -> NOT CONNECTED` | `python run_mvp.py --list`, then pass `--port "<name>"` |
| A CC never appears to move | it may only change for one frame. The monitor shows all values continuously; press `r` to re-send them all |
| MIDI learn won't bind a control | press that row's number key `1`–`9` *first*, then switch to the DAW and arm Learn — the CC keeps sweeping while you do |
| Learn binds the wrong CC | two mappings share one gesture (see below), or you used `r`. Use the number key, which mutes everything else |
| Finger count is off by one | run `--calibrate`, or `--debug` and compare straightness against the thresholds |
| A gesture never reaches 0 or 127 | its calibrated range is wrong for your hand — run `--calibrate` |
| Values jitter | raise `smoothing` or `deadzone` in the preset |
| Values feel laggy | lower `smoothing` |
