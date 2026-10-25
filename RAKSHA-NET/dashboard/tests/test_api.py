"""API smoke tests with FastAPI's TestClient (no hardware, no browser)."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("RAKSHA_DATA_DIR", tempfile.mkdtemp(prefix="raksha_test_"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402
import json  # noqa: E402

from backend.paths import RECORDINGS_DIR, SD_LOGS_DIR  # noqa: E402


def wait_for(fn, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.25)
    raise AssertionError("timed out")


def test_api_end_to_end():
    with TestClient(create_app()) as c:
        assert c.get("/").status_code == 200
        assert "RAKSHA-NET" in c.get("/").text
        assert c.get("/static/vendor/chart.umd.js").status_code == 200
        d = c.get("/api/defaults").json()
        assert d["settings"]["alert_threshold"] == 0.70 and d["settings"]["fall_confirm_samples"] == 3
        assert "ports" in c.get("/api/ports").json()

        # ML report is computed from the real files
        r = wait_for(lambda: (lambda j: j if j.get("ready") else None)(c.get("/api/ml").json()), 120)["report"]
        m = r["saved_model_test"]
        assert m["n_test"] == 357 and sum(map(sum, m["confusion_matrix"])) == 357
        assert r["reproduction"]["identical"] is True
        assert r["datasets"][0]["labels"] == {"straight": 855, "fall": 570}
        assert len(r["feature_importance"]) == 8

        # live start with a port that does not exist -> clear error, no crash
        assert c.post("/api/live/start", json={"port": "NO_SUCH_PORT_XYZ"}).status_code == 200
        st = wait_for(lambda: (lambda s: s if s["status"]["serial"]["state"] == "error" else None)(c.get("/api/state").json()))
        assert st["status"]["serial"]["error"]

        # validation
        assert c.post("/api/live/start", json={"port": "COM1", "settings": {"alert_threshold": 2}}).status_code == 400
        assert c.post("/api/demo/button").status_code == 400  # not in demo mode

        # demo replay produces samples through the prediction engine
        assert c.post("/api/demo/start", json={"source": "dataset"}).status_code == 200
        st = wait_for(lambda: (lambda s: s if len(s["samples"]) >= 3 else None)(c.get("/api/state").json()), 15)
        assert st["mode"] == "demo" and st["source"]["kind"] == "dataset"
        assert st["samples"][-1]["prediction"] in ("straight", "fall")
        assert c.post("/api/demo/button").json()["ok"] is False  # no alert open yet
        assert c.post("/api/demo/start", json={"source": "../../etc/passwd"}).status_code == 404
        assert c.post("/api/stop").status_code == 200
        assert c.get("/api/state").json()["mode"] == "idle"

        # replay of a recorded live session (test fixture written here, not real data)
        rec = RECORDINGS_DIR / "live_test_fixture.jsonl"
        lines = [{"type": "header", "format": "raksha-serial-v1", "created_text": "test", "port": "TEST"}]
        for i in range(4):
            lines.append({"t": i * 1.0, "dir": "rx", "line": "ACC X: 0.01 g, Y: -0.01 g, Z: 0.97 g | GYRO X: -2.61 dps, "
                                                               "Y: 0.49 dps, Z: -4.85 dps | Room: Bedroom RSSI: -64"})
            lines.append({"t": i * 1.0 + 0.01, "dir": "rx", "line": "MLDATA,0.0100,-0.0100,0.9700,-2.6100,0.4900,-4.8500"})
        rec.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        assert any(r["file"] == rec.name for r in c.get("/api/recordings").json()["recordings"])
        assert c.post("/api/demo/start", json={"source": rec.name}).status_code == 200
        st = wait_for(lambda: (lambda s: s if s["room"].get("room") == "Bedroom" and s["samples"] else None)(c.get("/api/state").json()), 15)
        assert st["room"]["rssi"] == -64 and st["source"]["kind"] == "recording"
        c.post("/api/stop")

        # SD card CSV import
        (SD_LOGS_DIR / "gsm_log.csv").write_text('Status,Response\n9000001111_SMS_SENT,"+CMGS: 3  OK"\n')
        files = c.get("/api/sdlogs").json()["files"]
        assert files[0]["header"] == ["Status", "Response"] and files[0]["total_rows"] == 1

        csv_text = c.get("/api/events.csv").text
        assert csv_text.startswith("id,time,mode,type")
        with c.websocket_connect("/ws") as ws:
            assert '"type": "snapshot"' in ws.receive_text()
