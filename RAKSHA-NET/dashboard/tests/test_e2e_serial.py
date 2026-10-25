"""End-to-end test over a virtual serial port (Linux/macOS only, about 3 minutes).

Starts tests/fake_esp32.py on a pty, starts run_dashboard.py connected to it,
and checks what the dashboard reports through its HTTP API.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[1]
PY = sys.executable

pytestmark = pytest.mark.skipif(os.name == "nt", reason="needs a pty")


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read())


def run_scenario(tmp_path, scenario, events, http_port):
    link = tmp_path / f"pty_{scenario}_{events}"
    fake = subprocess.Popen([PY, str(DASH / "tests" / "fake_esp32.py"), "--scenario", scenario, "--link", str(link)]
                            + (["--events"] if events else []), stdout=subprocess.PIPE, text=True)
    for _ in range(100):
        if link.exists() and link.read_text():
            break
        time.sleep(0.1)
    pty = link.read_text().strip()
    env = {**os.environ, "RAKSHA_DATA_DIR": str(tmp_path / "data")}
    dash = subprocess.Popen([PY, str(DASH / "run_dashboard.py"), "--no-browser", "--http-port", str(http_port),
                             "--serial-port", pty], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        t_end = time.time() + 240
        while time.time() < t_end:
            line = fake.stdout.readline()
            if line.strip() == "done" or not line:
                break
        time.sleep(6)  # let the dashboard infer the last outcome
        state = get(http_port, "/api/state")
        return state
    finally:
        dash.terminate()
        try:
            dash.wait(timeout=10)
        except subprocess.TimeoutExpired:
            dash.kill()
        fake.kill()


def titles(state):
    return [e["title"] for e in state["events"]]


@pytest.mark.parametrize("events", [False, True])
def test_confirm_scenario(tmp_path, events):
    st = run_scenario(tmp_path, "confirm", events, 8811 if not events else 8812)
    s = st["status"]
    assert s["serial"]["state"] == "connected"
    assert s["board"]["gsm"] == "ready" and s["board"]["sd"] == "ok" and s["board"]["mpu"] == "ok"
    assert s["board"]["event_reporting"] is events
    assert s["stats"]["mldata"] >= 60
    assert st["room"]["counts"].get("Hallroom", 0) > 0 and st["room"]["room"] == "Washroom"
    hist = st["alerts"]["history"]
    assert len(hist) == 2, [a["outcome"] for a in hist]
    first, second = hist
    assert first["outcome"] == ("cancelled" if events else "cancelled_inferred")
    assert first["sms"] == []
    assert second["outcome"] == "confirmed_sms_attempted"
    assert [a["status"] for a in second["sms"]] == ["accepted", "error"]
    if events:
        assert second["sms"][0]["firmware_status"] == "******1111_SMS_SENT"
        assert second["sms"][1]["firmware_status"] == "******2222_SMS_FAILED"
    assert "Fall confirmed: emergency SMS attempted" in titles(st)
    assert "Sensor data stream stopped" in titles(st)  # firmware blocked while sending SMS
    # no raw phone numbers leak into events or console
    blob = json.dumps(st)
    assert "9000001111" not in blob and "9000002222" not in blob


def test_no_gsm_scenario(tmp_path):
    st = run_scenario(tmp_path, "nogsm", False, 8813)
    hist = st["alerts"]["history"]
    assert st["status"]["board"]["gsm"] == "fail"
    assert len(hist) == 1 and hist[0]["outcome"] == "no_sms_gsm_fail"
    assert st["room"]["room"] == "Outside_Range"
