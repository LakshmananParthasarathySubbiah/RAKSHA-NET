"""TEST TOOL ONLY: a fake ESP32 on a virtual serial port (Linux/macOS pty).

It prints the same line formats as falldeetectml.ino and applies the same
rules to the PRED commands it receives (12 s cancel window, button cancel,
SMS to two numbers). Sensor values are rows from fall_dataset.csv. Room names,
RSSI values and modem replies are fixed test inputs, NOT measurements.

It exists so the dashboard's serial path can be tested without hardware.
It is never used by the dashboard and must not be shown as device data.

    python tests/fake_esp32.py --scenario confirm        # prints the pty path
    python tests/fake_esp32.py --scenario confirm --events   # behave like the event patch
"""
import argparse
import os
import random
import select
import sys
import time
import tty
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "ml_laptop" / "fall_dataset.csv"
WINDOW = 12.0


class FakeBoard:
    def __init__(self, fd, events, gsm_ready, sms_replies, presses):
        self.fd = fd
        self.events = events
        self.gsm_ready = gsm_ready
        self.sms_replies = list(sms_replies)
        self.presses = list(presses)  # seconds after alert start to press the button, per alert (None = no press)
        self.buf = b""
        self.last_pred_fall = False
        self.active = False
        self.started = 0.0
        self.alert_no = 0
        self.press_at = None
        self.room, self.rssi = "Outside_Range", 0

    def println(self, text):
        os.write(self.fd, (text + "\r\n").encode())

    def event(self, kind, status):
        if self.events:
            self.println(f"{kind},{status}")

    def boot(self):
        self.println("")
        self.println("RAKSHA-NET starting...")
        self.event("EVENT", "EVENT_REPORTING_ON")
        self.println("SD card ready.")
        if self.gsm_ready:
            self.println("GSM baud detected: 9600")
            self.println("OK")
            self.println("OK")
            self.println("GSM ready.")
        else:
            self.println("GSM AT failed.")
            self.event("GSMLOG", "GSM_INIT_FAILED")
        self.println("MPU6500 ready.")
        self.println("Beacon scanner started on core 0.")

    def poll_commands(self):
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                break
            try:
                data = os.read(self.fd, 1024)
            except OSError:
                return
            if not data:
                break
            self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            cmd = line.decode(errors="ignore").strip()
            if not cmd.startswith("PRED,"):
                continue
            status = cmd.split(",")[1].upper()
            if status == "FALL":
                if not self.last_pred_fall and not self.active:
                    self.active = True
                    self.started = time.time()
                    self.alert_no += 1
                    p = self.presses[self.alert_no - 1] if self.alert_no <= len(self.presses) else None
                    self.press_at = None if p is None else self.started + p
                    self.event("EVENT", "FALL_DETECTED_WAITING")
                self.last_pred_fall = True
            else:
                self.last_pred_fall = False

    def handle_alert(self):
        if not self.active:
            return
        now = time.time()
        if self.press_at is not None and now >= self.press_at:
            self.active = False
            self.event("EVENT", "FALL_CANCELLED_BUTTON")
            return
        if now - self.started >= WINDOW:
            self.active = False
            if self.gsm_ready:
                for number in ("9000001111", "9000002222"):
                    self.println("Sending GSM test SMS...")
                    time.sleep(1.5)
                    reply = self.sms_replies.pop(0) if self.sms_replies else ""
                    self.println(reply)
                    ok = "+CMGS:" in reply and "OK" in reply and "ERROR" not in reply
                    self.event("GSMLOG", f"{number}_{'SMS_SENT' if ok else 'SMS_FAILED'}")
            else:
                self.event("GSMLOG", "FALL_SMS_NOT_SENT")
            self.event("EVENT", "FALL_CONFIRMED_SMS_SENT")

    def wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.poll_commands()
            self.handle_alert()
            time.sleep(0.025)

    def sample(self, r):
        self.println(f"ACC X: {r.ax:.2f} g, Y: {r.ay:.2f} g, Z: {r.az:.2f} g | GYRO X: {r.gx:.2f} dps, "
                     f"Y: {r.gy:.2f} dps, Z: {r.gz:.2f} dps | Room: {self.room} RSSI: {self.rssi}")
        self.println(f"MLDATA,{r.ax:.4f},{r.ay:.4f},{r.az:.4f},{r.gx:.4f},{r.gy:.4f},{r.gz:.4f}")


SCENARIOS = {
    # alert 1 cancelled after 5 s, alert 2 confirmed with one accepted and one failed SMS
    "confirm": dict(gsm_ready=True, presses=[5.0, None],
                    sms=["+CMGS: 21\r\n\r\nOK", "+CMS ERROR: 38"],
                    phases=[("straight", 8, "Bedroom", -58), ("straight", 6, "Hallroom", -71),
                            ("fall", 8, "Hallroom", -69), ("straight", 14, "Hallroom", -70),
                            ("fall", 8, "Washroom", -64), ("straight", 30, "Washroom", -63)]),
    "nogsm": dict(gsm_ready=False, presses=[None], sms=[],
                  phases=[("straight", 6, "Bedroom", -60), ("fall", 8, "Bedroom", -60), ("straight", 25, "Outside_Range", 0)]),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="confirm", choices=SCENARIOS)
    ap.add_argument("--events", action="store_true", help="also print EVENT/GSMLOG lines like the event patch")
    ap.add_argument("--link", help="write the pty path to this file")
    args = ap.parse_args()
    sc = SCENARIOS[args.scenario]

    df = pd.read_csv(DATA)
    # Use rows that the trained model classifies as fall for the fall phases (test fixture).
    sys.path.insert(0, str(ROOT / "dashboard"))
    from backend.legacy import FEATURES, load_model
    model, _ = load_model(ROOT / "ml_laptop" / "fall_random_forest.joblib")
    proba = model.predict_proba(df[FEATURES])
    fall_col = list(model.classes_).index("fall")
    falls = df[(df.label == "fall") & (proba[:, fall_col] >= 0.9)].reset_index(drop=True)
    straights = df[(df.label == "straight") & (proba[:, fall_col] <= 0.1)].reset_index(drop=True)

    master, slave = os.openpty()
    tty.setraw(slave)
    path = os.ttyname(slave)
    if args.link:
        Path(args.link).write_text(path)
    print(path, flush=True)

    board = FakeBoard(master, args.events, sc["gsm_ready"], sc["sms"], sc["presses"])
    time.sleep(6.0)  # give the dashboard time to open the port
    board.boot()
    rnd = random.Random(1)
    i = j = 0
    for kind, seconds, room, rssi in sc["phases"]:
        board.room, board.rssi = room, rssi
        for _ in range(seconds):
            if kind == "fall":
                r = falls.iloc[i % len(falls)]
                i += 1
            else:
                r = straights.iloc[j % len(straights)]
                j += 1
            board.rssi = rssi if room == "Outside_Range" else rssi + rnd.randint(-3, 3)
            board.sample(r)
            board.wait(1.0)
    board.wait(3.0)
    print("done", flush=True)
    board.wait(600)  # keep the port open until the test stops this process


if __name__ == "__main__":
    main()
