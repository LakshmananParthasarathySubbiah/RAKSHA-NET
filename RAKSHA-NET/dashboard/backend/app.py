"""FastAPI application: REST controls, WebSocket stream and the static UI."""
import asyncio
import csv
import io
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ml_report
from .engine import EngineSettings
from .hub import Hub
from .legacy import PREDICT_LIVE_DEFAULTS, load_model
from .paths import MODEL_FILE, RECORDINGS_DIR, SD_LOGS_DIR, STATIC_DIR
from .sources import (DatasetReplayWorker, RecordingReplayWorker, SerialWorker, list_recordings,
                      list_serial_ports)


class SettingsIn(BaseModel):
    alert_threshold: float = PREDICT_LIVE_DEFAULTS["alert_threshold"]
    send_interval: float = PREDICT_LIVE_DEFAULTS["send_interval"]
    fall_confirm_samples: int = PREDICT_LIVE_DEFAULTS["fall_confirm_samples"]
    min_acc_mag: float = PREDICT_LIVE_DEFAULTS["min_acc_mag"]
    stale_seconds: float = PREDICT_LIVE_DEFAULTS["stale_seconds"]
    no_board_alert: bool = PREDICT_LIVE_DEFAULTS["no_board_alert"]

    def validated(self):
        if not 0.0 <= self.alert_threshold <= 1.0:
            raise HTTPException(400, "alert_threshold must be between 0 and 1")
        if self.fall_confirm_samples < 1:
            raise HTTPException(400, "fall_confirm_samples must be at least 1")
        if self.send_interval < 0 or self.stale_seconds <= 0 or self.min_acc_mag < 0:
            raise HTTPException(400, "intervals and thresholds must be positive")
        return self.model_dump()


class LiveIn(BaseModel):
    port: str
    baud: int = PREDICT_LIVE_DEFAULTS["baud"]
    settings: SettingsIn = SettingsIn()


class DemoIn(BaseModel):
    source: str = "dataset"  # "dataset" or a recording file name
    settings: SettingsIn = SettingsIn()


class RecordIn(BaseModel):
    enabled: bool


def create_app(default_settings: EngineSettings = None, startup=None):
    model, model_info = load_model(MODEL_FILE)
    model_info = {**model_info, "file": MODEL_FILE.name, "classes": [str(c) for c in model.classes_]}
    hub = Hub(model, model_info, default_settings or EngineSettings())
    ml_state = {"report": None, "error": None, "running": False}

    def build_report(force=False):
        ml_state["running"] = True
        try:
            ml_state["report"] = ml_report.get_report(force=force)
            ml_state["error"] = None
        except Exception as exc:  # shown in the UI
            ml_state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            ml_state["running"] = False

    @asynccontextmanager
    async def lifespan(app):
        hub.loop = asyncio.get_running_loop()
        hub.event("system", "info", "Dashboard started",
                  f"Model {MODEL_FILE.name} loaded (scikit-learn {model_info['running_sklearn']}).")
        if model_info.get("version_mismatch"):
            hub.event("system", "warning", "scikit-learn version differs from the one used to save the model",
                      f"Saved with {model_info['trained_sklearn']}, running {model_info['running_sklearn']}. "
                      "Install the version in requirements.txt.")
        threading.Thread(target=build_report, daemon=True).start()
        if startup:
            threading.Thread(target=startup, args=(app,), daemon=True).start()
        yield
        hub.stop_worker("Dashboard shutting down")

    app = FastAPI(title="RAKSHA-NET Dashboard", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    app.state.hub = hub

    # ---------------------------------------------------------------- REST
    @app.get("/api/ports")
    def ports():
        return {"ports": list_serial_ports()}

    @app.get("/api/state")
    def state():
        return hub.snapshot()

    @app.get("/api/defaults")
    def defaults():
        return {"settings": PREDICT_LIVE_DEFAULTS, "model": model_info}

    @app.post("/api/live/start")
    def live_start(body: LiveIn):
        settings = body.settings.validated()
        port = body.port.strip()
        if not port:
            raise HTTPException(400, "Choose a serial port")
        hub.start_worker("live", {"kind": "serial", "port": port, "baud": body.baud, "label": f"{port} @ {body.baud}",
                                  "settings": settings},
                         lambda h: SerialWorker(h, port, body.baud))
        return {"ok": True}

    @app.post("/api/demo/start")
    def demo_start(body: DemoIn):
        settings = body.settings.validated()
        if body.source == "dataset":
            hub.start_worker("demo", {"kind": "dataset", "label": "fall_dataset.csv (recorded training samples)",
                                      "settings": settings}, lambda h: DatasetReplayWorker(h))
        else:
            name = Path(body.source).name
            path = RECORDINGS_DIR / name
            if not path.exists():
                raise HTTPException(404, "Recording not found")
            hub.start_worker("demo", {"kind": "recording", "file": name, "label": f"Recorded session {name}",
                                      "settings": settings}, lambda h: RecordingReplayWorker(h, path))
        return {"ok": True}

    @app.post("/api/stop")
    def stop():
        hub.stop_worker()
        return {"ok": True}

    @app.post("/api/demo/button")
    def demo_button():
        if hub.mode != "demo":
            raise HTTPException(400, "The on-screen button only works in demo mode. In live mode use the device button.")
        return {"ok": hub.demo_button()}

    @app.post("/api/recording")
    def recording(body: RecordIn):
        hub.record_live = body.enabled
        hub.push_status()
        return {"ok": True, "note": "Applies from the next live connection."}

    @app.get("/api/recordings")
    def recordings():
        return {"recordings": list_recordings()}

    @app.get("/api/ml")
    def ml(refresh: bool = False):
        if refresh and not ml_state["running"]:
            threading.Thread(target=build_report, args=(True,), daemon=True).start()
        if ml_state["report"] is None:
            return JSONResponse({"ready": False, "error": ml_state["error"]}, status_code=202)
        return {"ready": True, "report": ml_state["report"]}

    @app.get("/api/events.csv")
    def events_csv():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return PlainTextResponse(hub.events_csv(), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="raksha_events_{stamp}.csv"'})

    @app.get("/api/sdlogs")
    def sdlogs():
        """CSV files copied from the MicroSD card into dashboard/sd_logs/."""
        files = []
        for p in sorted(SD_LOGS_DIR.glob("*.csv")):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                rows = list(csv.reader(io.StringIO(text)))
            except Exception as exc:
                files.append({"file": p.name, "error": str(exc)})
                continue
            files.append({"file": p.name, "header": rows[0] if rows else [], "rows": rows[1:2001],
                          "total_rows": max(0, len(rows) - 1)})
        return {"folder": str(SD_LOGS_DIR), "files": files}

    # ----------------------------------------------------------- WebSocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        q = asyncio.Queue()
        hub.listeners.add(q)
        import json
        try:
            await websocket.send_text(json.dumps(hub.snapshot()))
            while True:
                data = await q.get()
                await websocket.send_text(data)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            pass
        finally:
            hub.listeners.discard(q)

    # -------------------------------------------------------------- static
    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
