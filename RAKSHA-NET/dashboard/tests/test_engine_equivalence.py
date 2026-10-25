"""The dashboard engine must send exactly what predict_live.py sends.

Runs the ORIGINAL predict_live.main() against a fake serial port and a fake
clock, then runs backend.engine.PredictionEngine on the same lines and times,
and compares every byte written to the board.
"""
import ast
import random
import sys
from pathlib import Path

import pandas as pd
import pytest

DASH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DASH))

from backend.engine import EngineSettings, PredictionEngine  # noqa: E402
from backend.legacy import PREDICT_LIVE_DEFAULTS, load_model  # noqa: E402
from backend.paths import DATASET_FILE, MODEL_FILE, PREDICT_SCRIPT  # noqa: E402

import predict_live  # noqa: E402  (original script, imported through backend.legacy's sys.path)


class Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


class FakeSerial:
    def __init__(self, script, clock, writes):
        self.script = list(script)
        self.clock = clock
        self.writes = writes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def readline(self):
        if not self.script:
            raise KeyboardInterrupt
        dt, line = self.script.pop(0)
        self.clock.t += dt
        return line.encode()

    def write(self, data):
        self.writes.append((round(self.clock.t, 6), data))


def build_script(seed):
    rng = random.Random(seed)
    df = pd.read_csv(DATASET_FILE)
    straight = df[df.label == "straight"].reset_index(drop=True)
    fall = df[df.label == "fall"].reset_index(drop=True)
    script = [(0.2, "RAKSHA-NET starting..."), (0.1, "SD card ready."), (0.3, "GSM ready."),
              (0.1, "MPU6500 ready."), (0.1, "Beacon scanner started on core 0.")]

    def ml(r):
        return f"MLDATA,{r.ax:.4f},{r.ay:.4f},{r.az:.4f},{r.gx:.4f},{r.gy:.4f},{r.gz:.4f}"

    def text(r):
        return (f"ACC X: {r.ax:.2f} g, Y: {r.ay:.2f} g, Z: {r.az:.2f} g | GYRO X: {r.gx:.2f} dps, "
                f"Y: {r.gy:.2f} dps, Z: {r.gz:.2f} dps | Room: Bedroom RSSI: -61")

    si = fi = 0
    for block in range(30):
        use_fall = block % 3 == 2
        n = rng.randint(4, 25)
        for _ in range(n):
            if use_fall:
                r = fall.iloc[fi % len(fall)]
                fi += 1
            else:
                r = straight.iloc[si % len(straight)]
                si += 1
            script.append((rng.uniform(0.02, 0.1), text(r)))
            script.append((rng.uniform(0.85, 1.1), ml(r)))
            roll = rng.random()
            if roll < 0.05:
                script.append((1.0, ""))  # read timeout
            elif roll < 0.08:
                script.append((0.5, "MLDATA,0.0000,0.0000,0.0000,0.0000,0.0000,0.0000"))  # invalid sample
            elif roll < 0.10:
                script.append((0.3, "MPU6500 read failed."))
        if rng.random() < 0.35:
            # sensor stream stops for a while (e.g. firmware blocked sending SMS)
            for _ in range(rng.randint(2, 8)):
                script.append((1.0, ""))
            script.append((0.4, "Sending GSM test SMS..."))
            script.append((0.6, "+CMGS: 17"))
            script.append((0.0, "OK"))
    return script


def run_original(script, extra_args):
    clock = Clock(1000.0)
    writes = []
    orig_serial, orig_time, orig_argv = predict_live.serial.Serial, predict_live.time, sys.argv
    predict_live.serial.Serial = lambda *a, **k: FakeSerial(script, clock, writes)
    predict_live.time = clock
    sys.argv = ["predict_live.py", "--port", "FAKE", "--model", str(MODEL_FILE), *extra_args]
    try:
        predict_live.main()
    finally:
        predict_live.serial.Serial, predict_live.time, sys.argv = orig_serial, orig_time, orig_argv
    return writes


def run_engine(script, settings):
    model, _ = load_model(MODEL_FILE)
    eng = PredictionEngine(model, settings)
    t = 1000.0
    writes = []
    for dt, line in script:
        now = t  # time taken before the read, as in predict_live.py
        t += dt
        res = eng.process(line.strip(), now)
        if res.write is not None:
            writes.append((round(t, 6), res.write))
    final = PredictionEngine.shutdown_command(settings)
    if final:
        writes.append((round(t, 6), final))
    return writes


CASES = [
    ([], {}),
    (["--fall-confirm-samples", "5"], {"fall_confirm_samples": 5}),
    (["--alert-threshold", "0.9", "--send-interval", "2.5"], {"alert_threshold": 0.9, "send_interval": 2.5}),
    (["--stale-seconds", "1.5", "--min-acc-mag", "0.5"], {"stale_seconds": 1.5, "min_acc_mag": 0.5}),
    (["--no-board-alert"], {"no_board_alert": True}),
]


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("args,overrides", CASES)
def test_same_commands_as_predict_live(seed, args, overrides):
    script = build_script(seed)
    original = run_original(list(script), args)
    ours = run_engine(list(script), EngineSettings(**overrides))
    assert len(original) > 0 or overrides.get("no_board_alert")
    assert ours == original
    if not overrides.get("no_board_alert"):
        assert any(w[1].startswith(b"PRED,FALL") for w in original), "script should include confirmed falls"
        assert any(w[1] == b"PRED,CHECK,0.00\n" for w in original), "script should include stale periods"


def test_defaults_match_predict_live_source():
    tree = ast.parse(PREDICT_SCRIPT.read_text())
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            name = node.args[0].value.lstrip("-").replace("-", "_")
            for kw in node.keywords:
                if kw.arg == "default":
                    found[name] = ast.literal_eval(kw.value)
                if kw.arg == "action" and kw.value.value == "store_true":
                    found[name] = False
    for key, value in PREDICT_LIVE_DEFAULTS.items():
        assert found[key] == value, key
