"""Standalone MVP: webcam -> hand tracking -> MIDI CC -> Harmony Engine.

Runs in plain Python (no TouchDesigner) so you can prove the whole chain works
before building the TD patch. The TouchDesigner build reuses this same
`modules/` code unchanged -- only the capture front-end differs.

    pip install -r requirements.txt
    python run_mvp.py                     # auto-picks a MIDI port
    python run_mvp.py --calibrate         # fit the gesture ranges to your hand
    python run_mvp.py --list              # show MIDI ports and exit
    python run_mvp.py --port "loopMIDI Port" --preset default
    python run_mvp.py --debug             # overlay per-finger detection numbers

Keys in the video window:
    q      quit
    1..9   learn mode: sweep just that row's CC and mute the rest, so you can
           switch to the DAW and arm MIDI learn. Same key or 0 stops it.
    r      re-send every CC at once (checks the link is alive)
    space  panic -- send every touched CC to 0
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = os.path.join(HERE, "modules")

# Import the capture stack BEFORE modules/ joins sys.path, so a local file can
# never shadow the real third-party package.
try:
    import cv2
    import mediapipe as mp
except ImportError as exc:
    sys.exit(
        "Missing required package '%s'.\n"
        "Install the dependencies first:\n"
        "    pip install -r requirements.txt" % exc.name
    )

sys.path.insert(0, MODULES)

import config as cfg_mod  # noqa: E402
import gesture_detection as gd  # noqa: E402
from calibration import CalibrationWizard  # noqa: E402
from hand_landmarks import Landmarks  # noqa: E402
from midi_output import MidiSender, list_outputs  # noqa: E402
from parameter_mapping import MappingEngine  # noqa: E402

FONT = cv2.FONT_HERSHEY_SIMPLEX
FINGER_NAMES = ("thumb", "index", "middle", "ring", "pinky")
# Tried in order when no --port is given.
PREFERRED_PORTS = ("loopmidi", "iac", "harmony", "virtual")

WHITE = (255, 255, 255)
GREEN = (0, 255, 120)
GREY = (140, 140, 140)
AMBER = (0, 220, 255)
RED = (60, 60, 255)

# Learn mode holds one CC sweeping so you can leave this window, focus the DAW
# and arm MIDI learn without needing to press anything here mid-flight.
LEARN_TIMEOUT = 45.0   # seconds before it gives up on its own
LEARN_SWEEP = 1.2      # seconds per full 0 -> 127 -> 0 ramp


def parse_args():
    ap = argparse.ArgumentParser(description="Hand-gesture MIDI CC controller.")
    ap.add_argument("--preset", default="default", help="preset name from presets/")
    ap.add_argument("--port", default=None, help="MIDI out port name (substring match)")
    ap.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    ap.add_argument("--list", action="store_true", help="list MIDI outputs and exit")
    ap.add_argument("--calibrate", action="store_true",
                    help="fit gesture ranges to your hand, then exit")
    ap.add_argument("--debug", action="store_true", help="overlay finger-detection numbers")
    ap.add_argument("--no-window", action="store_true", help="headless; Ctrl-C to stop")
    return ap.parse_args()


# ---------------------------------------------------------------- setup


def load_config(preset_name):
    cfg = cfg_mod.Config()
    available = cfg.list_presets()
    if preset_name not in available:
        sys.exit(
            "No preset named '%s'. Available: %s"
            % (preset_name, ", ".join(available) or "(none found in presets/)")
        )
    cfg.load_preset(preset_name)
    cfg.load_calibration()
    if not cfg.mappings:
        sys.exit("Preset '%s' contains no mappings." % preset_name)
    return cfg


def pick_port(requested):
    ports = list_outputs()
    if not ports:
        return None
    if requested:
        for name in ports:
            if requested.lower() in name.lower():
                return name
        sys.exit(
            "No MIDI port matching '%s'. Available: %s" % (requested, ", ".join(ports))
        )
    for wanted in PREFERRED_PORTS:
        for name in ports:
            if wanted in name.lower():
                return name
    return ports[0]


def open_midi(cfg, requested):
    midi = MidiSender(
        port_name=cfg.settings.get("midi_port_name", "loopMIDI Port"),
        channel=cfg.settings.get("midi_channel", 1),
        min_interval_ms=cfg.settings.get("min_send_interval_ms", 8),
    )
    port = pick_port(requested)
    if port and midi.open(port):
        print("MIDI -> %s" % midi.status)
    elif midi.open_virtual("HarmonyGesture"):
        print("MIDI -> virtual port 'HarmonyGesture'")
    else:
        print("MIDI -> NOT CONNECTED (%s). Gestures still display." % midi.status)
    return midi


def open_camera(index):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        sys.exit("Could not open camera %d. Try a different --camera index." % index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 60)
    return cap


def open_tracker():
    return mp.solutions.hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        model_complexity=0,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.6,
    )


def read_hand(cap, hands):
    """One mirrored frame plus its landmarks. Returns (frame, raw_hand, Landmarks)."""
    ok, frame = cap.read()
    if not ok:
        return None, None, Landmarks()
    frame = cv2.flip(frame, 1)  # mirror, so moving right reads as right
    result = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    hand = result.multi_hand_landmarks[0] if result.multi_hand_landmarks else None
    lm = Landmarks([(p.x, p.y, p.z) for p in hand.landmark]) if hand else Landmarks()
    return frame, hand, lm


# ---------------------------------------------------------------- display


def draw_overlay(frame, name, rows, changed, fps):
    """Every mapping, every frame. `changed` holds the CCs that moved.

    The leading digit is the key that re-sends that row on its own, which is
    how you bind it in a DAW without waiting for the gesture to move it.
    """
    cv2.putText(frame, name, (12, 32), FONT, 0.9, GREEN, 2)
    cv2.putText(frame, "%.0f fps" % fps, (frame.shape[1] - 88, 26), FONT, 0.5, GREY, 1)
    for i, (cc, value, lbl) in enumerate(rows[:9]):
        shown = "--" if value is None else "%3d" % value
        cv2.putText(frame, "%d  %-14s CC%-3d %s" % (i + 1, lbl[:14], cc, shown),
                    (12, 64 + i * 22), FONT, 0.5,
                    GREEN if cc in changed else WHITE, 1)


def draw_debug(frame, feats):
    """Per-finger straightness and the thumb gap, for tuning the thresholds."""
    x = frame.shape[1] - 200
    cv2.putText(frame, "straightness", (x, 56), FONT, 0.5, AMBER, 1)
    for i, finger in enumerate(FINGER_NAMES):
        raised = feats["_fingers"][i]
        cv2.putText(
            frame,
            "%-7s %.2f %s" % (finger, feats["_straightness"][i], "UP" if raised else "--"),
            (x, 80 + i * 20), FONT, 0.45, GREEN if raised else GREY, 1,
        )
    cv2.putText(frame, "thumb gap %.2f" % feats["_thumb_abduction"],
                (x, 200), FONT, 0.45, AMBER, 1)
    cv2.putText(frame, "count     %d" % feats["_finger_count_raw"],
                (x, 220), FONT, 0.45, AMBER, 1)


def _stop_learn(midi, rows, index, why):
    """Leave learn mode and put the CC back where the gesture actually has it."""
    cc, value, lbl = rows[index]
    if value is not None:
        midi.send_cc(cc, value, force=True)
    print("\nlearn %s: %s (CC%d)" % (why, lbl, cc))
    return None


def draw_learn_banner(frame, row, seconds_left):
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 46), RED, -1)
    cv2.putText(frame, "LEARNING %s  CC%d  -  arm MIDI learn now  (%ds)"
                % (row[2], row[0], seconds_left), (12, 30), FONT, 0.55, WHITE, 2)


# ---------------------------------------------------------------- calibrate


def run_calibration(cfg, cap, hands):
    """Guided pose capture. Writes calibration.json and returns True on success."""
    wizard = CalibrationWizard(cfg)
    wizard.start()
    print("Calibrating. Hold each pose until the bar fills. Press q to abort.")

    last_step = None
    while wizard.active:
        frame, hand, lm = read_hand(cap, hands)
        if frame is None:
            print("Camera stopped delivering frames.")
            return False

        if wizard.step_name != last_step:
            last_step = wizard.step_name
            print("  %s" % wizard.prompt)
        wizard.update(lm)

        if hand is not None:
            mp.solutions.drawing_utils.draw_landmarks(
                frame, hand, mp.solutions.hands.HAND_CONNECTIONS)
        cv2.putText(frame, wizard.prompt, (12, 32), FONT, 0.6, AMBER, 2)
        width = int((frame.shape[1] - 24) * wizard.progress)
        cv2.rectangle(frame, (12, 46), (12 + width, 62),
                      GREEN if hand is not None else GREY, -1)
        if hand is None:
            cv2.putText(frame, "no hand visible", (12, 88), FONT, 0.6, GREY, 2)
        cv2.imshow("Calibration", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            wizard.cancel()
            print("Calibration cancelled; nothing written.")
            return False

    cv2.destroyWindow("Calibration")
    cal = cfg.calibration
    print("\nSaved calibration.json:")
    for key in ("straight_threshold", "thumb_straight_threshold",
                "thumb_abduction_threshold", "pinch_min", "pinch_max",
                "spread_min", "spread_max", "rotation_min", "rotation_max"):
        print("  %-26s %.3f" % (key, cal[key]))
    return True


# ---------------------------------------------------------------- main


def main():
    args = parse_args()

    if args.list:
        ports = list_outputs()
        print("\n".join("%d: %s" % (i, p) for i, p in enumerate(ports))
              or "No MIDI outputs found.")
        return 0

    cfg = load_config(args.preset)

    if args.calibrate:
        cap, hands = open_camera(args.camera), open_tracker()
        try:
            ok = run_calibration(cfg, cap, hands)
        finally:
            cap.release()
            hands.close()
            cv2.destroyAllWindows()
        return 0 if ok else 1

    engine = MappingEngine(cfg.mappings)
    midi = open_midi(cfg, args.port)
    cap = open_camera(args.camera)
    hands = open_tracker()
    drawer = mp.solutions.drawing_utils

    print("Preset '%s': %d mappings. %s"
          % (cfg.preset_name, len(cfg.mappings),
             "Ctrl-C to stop." if args.no_window
             else "q quit, 1-9 learn one CC, r re-send all."))

    fps = 0.0
    last_frame = time.monotonic()
    last_print = 0.0
    learn_index = None
    learn_started = 0.0

    try:
        while True:
            frame, hand, lm = read_hand(cap, hands)
            if frame is None:
                print("\nCamera stopped delivering frames.")
                break

            now = time.monotonic()
            elapsed = now - last_frame
            last_frame = now
            if elapsed > 0:
                fps = fps * 0.9 + 0.1 / elapsed

            feats = gd.compute_features(lm, cfg.calibration)
            name = gd.label(feats)
            sent = engine.update(feats)          # keep smoothers advancing
            rows = engine.snapshot()
            changed = {cc for cc, _, _ in sent}

            if learn_index is None:
                midi.send_batch(sent)
            elif now - learn_started > LEARN_TIMEOUT:
                learn_index = _stop_learn(midi, rows, learn_index, "timed out")
            else:
                # Sweep this one CC and mute the rest, so the DAW has exactly
                # one moving control to bind -- and keep sweeping, because you
                # cannot press a key here while Reaper has focus.
                cc = rows[learn_index][0]
                phase = ((now - learn_started) % LEARN_SWEEP) / LEARN_SWEEP
                midi.send_cc(cc, int(127 * (1.0 - abs(2.0 * phase - 1.0))), force=True)

            if now - last_print > 0.25:
                last_print = now
                values = " ".join(
                    "%d:%s" % (cc, "--" if v is None else v) for cc, v, _ in rows)
                print("\r%-11s %-84s" % (name, values[:84]), end="", flush=True)

            if args.no_window:
                continue

            if hand is not None:
                drawer.draw_landmarks(frame, hand, mp.solutions.hands.HAND_CONNECTIONS)
            draw_overlay(frame, name, rows, changed, fps)
            if args.debug and feats:
                draw_debug(frame, feats)
            if learn_index is not None:
                draw_learn_banner(frame, rows[learn_index],
                                  int(LEARN_TIMEOUT - (now - learn_started)))
            cv2.imshow("Harmony Gesture MVP", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                print("\nre-sent all %d CC values" % midi.resend(rows))
            if key == ord("0") and learn_index is not None:
                learn_index = _stop_learn(midi, rows, learn_index, "stopped")
            if ord("1") <= key <= ord("9"):
                i = key - ord("1")
                if i >= len(rows):
                    pass
                elif learn_index == i:
                    learn_index = _stop_learn(midi, rows, i, "stopped")
                else:
                    learn_index, learn_started = i, now
                    print("\nLEARN %s (CC%d) sweeping for %ds -- switch to your "
                          "DAW now and arm MIDI learn. 0 or the same key stops."
                          % (rows[i][2], rows[i][0], int(LEARN_TIMEOUT)))
            if key == ord(" "):
                midi.panic()
                print("\npanic: all CC -> 0")
    except KeyboardInterrupt:
        pass
    finally:
        midi.panic()
        midi.close()
        cap.release()
        hands.close()
        if not args.no_window:
            cv2.destroyAllWindows()
    print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
