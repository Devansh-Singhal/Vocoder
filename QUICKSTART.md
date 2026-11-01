# QUICKSTART — get a working MVP today

Assumes zero prior knowledge. Two stages: **Stage A** proves the whole chain
works with plain Python in ~20 minutes. **Stage B** moves the same code into
TouchDesigner for the polished UI. Do not start Stage B until Stage A works.

---

## What you are building

```
webcam  →  hand tracking  →  gesture numbers 0..1  →  MIDI CC messages
        →  loopMIDI  →  Reaper  →  Antares Harmony Engine on the MIC track only
```

Your backing track lives on a **different** Reaper track with **no plugin** on
it, so gestures can never touch it.

This guide uses **Reaper**. Ableton works too — only steps A5 and A6 differ.

---

## Stage A — the 20-minute MVP (no TouchDesigner)

### A1. Install Python
Install Python 3.11 or newer from python.org. On Windows tick
**"Add Python to PATH"**. Check it worked — open Terminal (macOS) or Command
Prompt (Windows):

```
python --version
```

### A2. Install the libraries

From the project folder:

```
pip install -r requirements.txt
```

### A3. Create the virtual MIDI cable
This is a fake cable that carries MIDI from this app into Reaper.

- **Windows**: install **loopMIDI** (free, tobias-erichsen.de). Open it, click
  the **+** button. You now have a port called `loopMIDI Port`. Leave it running.
- **macOS**: nothing to install — the script creates its own port named
  `HarmonyGesture`.

### A4. Run it

```
python run_mvp.py
```

A video window opens. Move your hand in front of the camera. You should see:
- a skeleton drawn on your hand,
- the gesture name ("open hand", "fist", "pinch"),
- every mapping listed live, e.g. `Voice Gain  CC20  84`, with the ones that
  just moved picked out in green.

If the numbers move when your hand moves, **the MVP works**. Keys: `q` quit,
`1`–`9` learn mode for that row, `r` re-send all, `space` panic (all CC to 0).
Each row is numbered with its key.

### A4b. Calibrate to your hand

Do this once, before MIDI learn:

```
python run_mvp.py --calibrate
```

Seven poses — open hand, fist, pinch closed, pinch open, tilt left, tilt
right, then a slow sweep of your hand toward and away from the camera. It
writes `calibration.json`, setting every range from *your* hand instead of
shipped constants. Without it, gestures work but may not reach the full 0–127.

Troubleshooting:
- `Could not open camera 0` → try `python run_mvp.py --camera 1`.
- `MIDI -> NOT CONNECTED` → run `python run_mvp.py --list` to see port names,
  then `python run_mvp.py --port "loopMIDI Port"`.
- Finger count looks wrong → run `--calibrate` (A4b). If it still misreads,
  `--debug` shows the live straightness number behind each finger's raised/not
  decision.

### A5. Set up Reaper

**Audio device first.** Options → Preferences → Audio → Device. On Windows pick
**ASIO** and your interface's own driver (ASIO4ALL if it has none). Set the
block size to **64 or 128 samples** — this is your singing latency, and nothing
later in this guide can compensate for getting it wrong.

**Track 1 — your voice.**
1. Track → Insert new track. Name it `Vocal`.
2. Click the **record-arm** button (round button on the track panel), then set
   the input to your microphone channel from the dropdown next to it.
3. Click the **monitoring** button (the speaker icon on the arm button) until it
   reads *Record: input*, so you hear yourself through the plugin.
4. Click **FX** on that track → add **Antares Harmony Engine**.

**Track 2 — the backing track.**
1. Insert a second track, name it `Backing`.
2. Drag your instrumental audio file onto it.
3. **Add no FX, and never route it into `Vocal`.** This is the whole reason the
   instrumental stays untouched — it simply never meets the plugin.

**Enable the MIDI input.**
Options → Preferences → **Audio → MIDI Devices**. Find `loopMIDI Port` under
*MIDI inputs*, right-click it → **Enable input**. Right-click again → **Enable
input for control messages**. Close preferences.

If `loopMIDI Port` isn't listed, loopMIDI wasn't running when Reaper started —
launch loopMIDI, then restart Reaper.

### A6. Teach Harmony Engine your gestures (MIDI learn)

Set Harmony Engine's key and scale first, or it has nothing to harmonise to.
Note that these CCs drive Harmony Engine's **parameters** — they are not the
harmony notes themselves.

Leave `run_mvp.py` running. **Don't gesture at the Learn dialog** — use learn
mode instead, and note that the order of these steps matters:

1. Click the video window, press the row's number key (`1`–`9`).
2. A red banner appears; that CC starts sweeping and the other eight go quiet.
3. Click into Reaper. Open the FX window on the `Vocal` track, select Harmony
   Engine, click **Param** in its toolbar → **Learn**. It binds straight away.
4. Back in the video window, press the same key (or `0`) to stop.
5. Repeat for the next row.

You have 45 seconds, and it stops on its own after that. This is the only way
that works: the video window only receives keystrokes while *it* has focus, and
Reaper only learns while *Reaper* has focus — so the sweep has to be running
before you switch across.

`r` is not for this. It re-sends all nine at once, which proves the MIDI link
is alive but gives Reaper nine simultaneous CCs to choose between.

| Gesture                | CC | Suggested Harmony Engine target |
|------------------------|----|---------------------------------|
| hand height (up/down)  | 20 | Voice 1-4 Gain (all four)       |
| hand left/right        | 21 | Voice 1-4 Throat Length         |
| palm rotation (tilt)   | 22 | Spread                          |
| pinch (thumb+index)    | 23 | Glide Transition Rate           |
| finger count           | 24 | Choir Size (6 steps)            |
| open hand (all five)   | 25 | Choir Bypass (inverted)         |
| closed fist            | 26 | Reaper FX Bypass (kill switch)  |
| finger spread          | 27 | Choir Vibrato                   |
| hand distance to camera| 28 | Humanize Pitch Variation        |

Learn them in the order listed: `--preset minimal_live` gives you just the
first three (voice gain, glide rate, kill switch), which is fewer things to
diagnose if something misbehaves. Switch to `--preset default` for the rest.

Throat Length on CC 21 is very sensitive, so it ships clamped to `min: 40,
max: 90` instead of a full sweep. Widen it in the preset once you know how far
you want to travel.

That's the MVP: sing, move your hand, Harmony Engine responds, backing track
untouched.

---

## Stage B — move it into TouchDesigner (the real UI)

Only after Stage A works. Follow **Building the TouchDesigner patch** in
`README.md`, steps 1–6. The Python in `modules/` is identical, so nothing you
learned is thrown away — TD only replaces the webcam capture and gives you the
panel, presets and calibration wizard.

Short version:
1. Install TouchDesigner (free non-commercial licence).
2. New project → `Video Device In TOP` for the webcam.
3. Palette (`Alt+L`) → Derivative → Tools → **handTracking** → point it at the webcam.
4. Add a Base COMP, drop `modules/*.py` in as Text DATs, set
   `gesture_engine_ext.py` as its extension.
5. The loopMIDI and Reaper setup from A3–A6 carries over unchanged — TouchDesigner
   sends the same CCs on the same port, so your MIDI learn assignments survive.

---

## Tweaking without touching code

Edit `presets/default.json`: change any `cc`, `min`, `max`, `invert`,
`smoothing` (higher = smoother/slower) or `deadzone` (higher = ignores more
jitter), then restart the script. Copy the file to make your own preset and run
`python run_mvp.py --preset myname`.
