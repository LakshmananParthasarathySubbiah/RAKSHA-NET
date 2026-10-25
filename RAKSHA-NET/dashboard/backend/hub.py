"""Central state for the dashboard.

Exactly one data source feeds the hub at a time:
  * SerialWorker  (live mode, owns the serial port), or
  * ReplayWorker  (demo mode, replays recorded data; no serial port is opened).

Each line goes through the same path in both modes:
  line -> classify -> board / room / alert state -> PredictionEngine -> command
"""
import asyncio
import csv
import io
import itertools
import json
import threading
import time
from collections import deque
from datetime import datetime

from .alerts import AlertTracker, FirmwareEmulator
from .engine import EngineSettings, PredictionEngine
from .lines import classify, mask_number
from .paths import LOGS_DIR, RECORDINGS_DIR

KNOWN_ROOMS = ["Bedroom", "Hallroom", "Washroom"]  # ROOM_BEACON_NAMES in the firmware, without "ROOM_"
OUTSIDE = "Outside_Range"


class Recorder:
    """Writes every received line and every command sent, with timestamps, for later replay."""

    def __init__(self, meta):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = RECORDINGS_DIR / f"live_{stamp}.jsonl"
        self.t0 = time.time()
        self.fh = self.path.open("w", encoding="utf-8")
        self.count = 0
        self._write({"type": "header", "format": "raksha-serial-v1", "created": self.t0,
                     "created_text": datetime.now().isoformat(timespec="seconds"), **meta})

    def _write(self, obj):
        self.fh.write(json.dumps(obj) + "\n")
        self.fh.flush()

    def line(self, direction, text, now):
        self.count += 1
        self._write({"t": round(now - self.t0, 3), "dir": direction, "line": text})

    def close(self):
        try:
            self.fh.close()
        except Exception:
            pass


class Hub:
    def __init__(self, model, model_info, default_settings: EngineSettings):
        self.model = model
        self.model_info = model_info
        self.default_settings = default_settings
        self.lock = threading.RLock()
        self.loop = None
        self.listeners = set()
        self.events = deque(maxlen=3000)
        self._event_ids = itertools.count(1)
        self.event_file = (LOGS_DIR / f"events_{datetime.now():%Y%m%d}.jsonl").open("a", encoding="utf-8")
        self.worker = None
        self.recorder = None
        self.record_live = True
        self._reset("idle", {})
        self._ticker_stop = threading.Event()
        threading.Thread(target=self._ticker, name="ticker", daemon=True).start()

    # ------------------------------------------------------------------ state
    def _reset(self, mode, source):
        settings = EngineSettings(**source.get("settings", self.default_settings.as_dict()))
        self.mode = mode  # idle | live | demo
        self.source = {k: v for k, v in source.items() if k != "settings"}
        self.settings = settings
        self.engine = PredictionEngine(self.model, settings)
        self.board = {"booted_seen": False, "sd": "unknown", "gsm": "unknown", "gsm_baud": None,
                      "mpu": "unknown", "beacon_scanner": "unknown", "event_reporting": False}
        demo_dataset = mode == "demo" and source.get("kind") == "dataset"
        self.tracker = AlertTracker(self._emit, self.board, demo=demo_dataset)
        if demo_dataset:
            self.board["event_reporting"] = True
            self.emulator = FirmwareEmulator(self._emulator_feed)
        else:
            self.emulator = None
        self.serial = {"state": "disconnected", "port": source.get("port"), "baud": source.get("baud"),
                       "error": None, "connected_at": None}
        self.stats = {"lines_rx": 0, "mldata": 0, "sensor_text": 0, "invalid": 0, "malformed": 0,
                      "commands_sent": 0, "last_line_at": None, "last_sample_at": None,
                      "started_at": time.time()}
        self.samples = deque(maxlen=900)
        self.console = deque(maxlen=500)
        self.room = {"available": False, "room": None, "rssi": None, "at": None, "since": None}
        self.room_history = deque(maxlen=900)
        self.room_counts = {}
        self.stream_stalled = False
        self.episode = None  # suspected-fall episode
        self.latest = None
        self.last_command = None

    def snapshot(self):
        with self.lock:
            return {
                "type": "snapshot",
                "server_time": time.time(),
                "mode": self.mode,
                "source": self.source,
                "status": self._status(),
                "samples": list(self.samples)[-300:],
                "console": list(self.console)[-300:],
                "room": self._room_state(),
                "room_history": list(self.room_history)[-300:],
                "alerts": self.tracker.snapshot(),
                "events": list(self.events)[-600:],
            }

    def _status(self):
        now = time.time()
        recent = [s for s in self.samples if now - s["t"] <= 10]
        rate = None
        if len(recent) >= 2:
            span = recent[-1]["t"] - recent[0]["t"]
            rate = (len(recent) - 1) / span if span > 0 else None
        return {
            "mode": self.mode,
            "source": self.source,
            "serial": dict(self.serial),
            "board": dict(self.board),
            "stats": dict(self.stats),
            "sample_rate_hz": rate,
            "stream_stalled": self.stream_stalled,
            "settings": self.settings.as_dict(),
            "model": self.model_info,
            "recording": {"enabled": self.record_live,
                          "file": self.recorder.path.name if self.recorder else None,
                          "lines": self.recorder.count if self.recorder else 0},
            "latest": self.latest,
            "last_command": self.last_command,
            "server_time": now,
        }

    def _room_state(self):
        return {**self.room, "known_rooms": KNOWN_ROOMS, "counts": dict(self.room_counts)}

    # --------------------------------------------------------------- emitting
    def _emit(self, kind, payload):
        if kind == "event":
            ev = {"id": next(self._event_ids), "mode": self.mode,
                  "source_name": self.source.get("label"), **payload}
            ev["detail"] = mask_number(ev.get("detail") or "")
            self.events.append(ev)
            try:
                self.event_file.write(json.dumps(ev) + "\n")
                self.event_file.flush()
            except Exception:
                pass
            self._broadcast({"type": "event", "event": ev})
        elif kind == "alert":
            self._broadcast({"type": "alert", "alert": payload, "server_time": time.time()})
        else:
            self._broadcast({"type": kind, **payload})

    def event(self, etype, severity, title, detail="", source="dashboard", t=None):
        with self.lock:
            self._emit("event", {"t": t or time.time(), "type": etype, "severity": severity,
                                 "title": title, "detail": detail, "source": source})

    def _broadcast(self, msg):
        if self.loop is None:
            return
        data = json.dumps(msg)
        for q in list(self.listeners):
            try:
                self.loop.call_soon_threadsafe(self._put, q, data)
            except RuntimeError:
                pass

    @staticmethod
    def _put(q, data):
        if q.qsize() < 2000:
            q.put_nowait(data)

    def push_status(self):
        with self.lock:
            self._broadcast({"type": "status", "status": self._status()})

    # ---------------------------------------------------------- line handling
    def process_rx(self, line, now):
        """Handle one stripped line ('' = read timeout). Returns bytes to write to the board, or None."""
        with self.lock:
            src = "serial" if self.mode == "live" else "replay"
            if line:
                self.stats["lines_rx"] += 1
                self.stats["last_line_at"] = now
                self._console("rx", line, now)
                if self.recorder:
                    self.recorder.line("rx", line, now)

            kind, data = classify(line)
            self._handle_kind(kind, data, line, now, src)

            res = self.engine.process(line, now)
            if res.malformed:
                self.stats["malformed"] += 1
                self._emit("event", {"t": now, "type": "sensor", "severity": "warning",
                                     "title": "Malformed MLDATA line skipped", "detail": line[:120], "source": src})
            if res.kind == "invalid":
                self.stats["invalid"] += 1
                if self.stats["invalid"] in (1, 10, 100) or self.stats["invalid"] % 500 == 0:
                    self._emit("event", {"t": now, "type": "sensor", "severity": "warning",
                                         "title": "Invalid sensor sample ignored",
                                         "detail": f"acc_mag below {self.settings.min_acc_mag} g and gyro_mag <= 0.5 dps "
                                                   f"(as in predict_live.py). Total so far: {self.stats['invalid']}.",
                                         "source": src})
            elif res.kind == "sample":
                self._on_sample(res, now)

            if res.write is not None:
                self._sent(res.write.decode(), now)
            return res.write

    def _sent(self, cmd, now):
        self.stats["commands_sent"] += 1
        self.last_command = {"command": cmd.strip(), "t": now}
        self._console("tx", cmd.strip(), now)
        if self.recorder:
            self.recorder.line("tx", cmd.strip(), now)
        self.tracker.on_tx(cmd, now, self.room.get("room"), self.room.get("rssi"))
        if self.emulator:
            self.emulator.on_tx(cmd, now)

    def _console(self, direction, text, now):
        item = {"t": now, "dir": direction, "text": mask_number(text)}
        self.console.append(item)
        self._broadcast({"type": "console", "item": item})

    def _handle_kind(self, kind, data, line, now, src):
        b = self.board
        ev = lambda *a: self._emit("event", {"t": now, "type": a[0], "severity": a[1], "title": a[2],
                                             "detail": a[3] if len(a) > 3 else "", "source": src})
        changed = True
        if kind in ("mldata", "sensor_text"):
            changed = False
            self.tracker.on_sample(now)
            if kind == "sensor_text":
                self.stats["sensor_text"] += 1
                if data:
                    self._room_update(data["room"], data["rssi"], now, src)
            if b["mpu"] == "unknown":
                b["mpu"] = "ok"
                changed = True
        elif kind == "boot":
            if self.tracker.current is not None:
                self.tracker._close(now, "board_restarted", "Board restarted while an alert was open.",
                                    src, severity="warning", title="Fall alert interrupted by board restart")
            self.tracker.board_last_pred_fall = False
            b.update({"booted_seen": True, "sd": "unknown", "gsm": "unknown", "gsm_baud": None,
                      "mpu": "unknown", "beacon_scanner": "unknown"})
            ev("system", "info", "Board started", "Firmware printed 'RAKSHA-NET starting...'")
        elif kind == "sd_ok":
            b["sd"] = "ok"
            ev("sd", "info", "SD card ready")
        elif kind == "sd_fail":
            b["sd"] = "fail"
            ev("sd", "warning", "SD card mount failed", "Firmware cannot write fall_log/ai_fall_log/gsm_log.")
        elif kind == "sd_write_fail":
            ev("sd", "warning", f"SD write failed: {data}")
        elif kind == "gsm_baud":
            b["gsm_baud"] = data
            ev("gsm", "info", f"GSM baud detected: {data}")
        elif kind == "gsm_ready":
            b["gsm"] = "ready"
            ev("gsm", "info", "GSM ready", "SIM800L answered AT commands at boot. This does not test network registration.")
        elif kind == "gsm_fail":
            b["gsm"] = "fail"
            ev("gsm", "warning", "GSM init failed", line)
        elif kind == "mpu_ok":
            b["mpu"] = "ok"
            ev("sensor", "info", "MPU6500 ready")
        elif kind == "mpu_missing":
            b["mpu"] = "missing"
            ev("sensor", "critical", "MPU6500 not found", "Firmware halts in setup(). Check SDA=GPIO8, SCL=GPIO9, 3.3 V, GND.")
        elif kind == "mpu_read_fail":
            b["mpu"] = "read_fail"
            ev("sensor", "warning", "MPU6500 read failed", "I2C read returned fewer than 14 bytes.")
        elif kind == "beacon_started":
            b["beacon_scanner"] = "running"
            ev("room", "info", "BLE beacon scanner started")
        elif kind in ("fw_event", "fw_gsmlog"):
            if not b["event_reporting"]:
                b["event_reporting"] = True
            if kind == "fw_event" and data == "EVENT_REPORTING_ON":
                ev("system", "info", "Firmware event reporting is on")
            elif kind == "fw_gsmlog" and data == "GSM_INIT_FAILED":
                b["gsm"] = "fail"
                ev("gsm", "warning", "GSM init failed (GSMLOG)")
            elif kind == "fw_gsmlog" and data.endswith(("_SMS_SENT", "_SMS_FAILED")):
                ev("gsm", "info" if data.endswith("_SMS_SENT") else "warning",
                   "Firmware SMS log: " + mask_number(data),
                   "Firmware isSMSSent() result written to gsm_log.csv. SMS_SENT means the modem replied "
                   "+CMGS/OK; delivery to the phone is not reported.")
            elif kind == "fw_gsmlog" and data == "FALL_SMS_NOT_SENT":
                ev("gsm", "warning", "SMS not sent: GSM not ready")
            self.tracker.on_rx(kind, data, line, now)
        elif kind in ("sms_start", "other"):
            changed = kind == "sms_start"
            if kind == "sms_start" and b["gsm"] != "ready":
                # sendConfirmedFallAlert() only calls sendSMS() when gsmReady is true
                b["gsm"] = "ready"
                ev("gsm", "info", "GSM was ready at boot (inferred)",
                   "The firmware only attempts SMS when initGSM() succeeded.")
            self.tracker.on_rx(kind, data, line, now)
        else:
            changed = False
        if changed:
            self._broadcast({"type": "status", "status": self._status()})

    def _room_update(self, room, rssi, now, src):
        r = self.room
        prev = r.get("room")
        r["available"] = True
        r["rssi"] = rssi if room != OUTSIDE else None
        r["at"] = now
        if room != prev:
            r["room"] = room
            r["since"] = now
            if prev is not None:
                self._emit("event", {"t": now, "type": "room", "severity": "info",
                                     "title": f"Room changed: {prev} -> {room}",
                                     "detail": f"RSSI {rssi} dBm" if room != OUTSIDE else "No known beacon above -95 dBm",
                                     "source": src})
        self.room_counts[room] = self.room_counts.get(room, 0) + 1
        item = {"t": now, "room": room, "rssi": r["rssi"]}
        self.room_history.append(item)
        self._broadcast({"type": "room", "room": self._room_state(), "item": item})

    def _on_sample(self, res, now):
        ax, ay, az, gx, gy, gz, acc_mag, gyro_mag = [float(v) for v in res.row]
        s = self.settings
        if res.prediction == "straight":
            state = "normal"
        elif res.fall_count >= s.fall_confirm_samples:
            state = "fall"
        elif res.fall_count >= 1:
            state = "suspected"
        else:
            state = "check"
        sample = {"t": now, "ax": ax, "ay": ay, "az": az, "gx": gx, "gy": gy, "gz": gz,
                  "acc_mag": acc_mag, "gyro_mag": gyro_mag, "prediction": res.prediction,
                  "confidence": res.probability, "p_fall": res.p_fall, "fall_count": res.fall_count,
                  "state": state, "command": res.command.strip(), "sent": res.write is not None}
        self.samples.append(sample)
        self.latest = sample
        self.stats["mldata"] += 1
        self.stats["last_sample_at"] = now
        if self.stream_stalled:
            self.stream_stalled = False
            self._emit("event", {"t": now, "type": "sensor", "severity": "info",
                                 "title": "Sensor data stream resumed", "detail": "", "source": "dashboard"})
        # Suspected-fall episodes (for the history; the board only acts on PRED,FALL)
        if res.fall_count >= 1 and self.episode is None:
            self.episode = {"start": now, "max": res.fall_count}
            self._emit("event", {"t": now, "type": "fall", "severity": "warning", "title": "Suspected fall",
                                 "detail": f"Model predicted fall with confidence {res.probability:.2f} "
                                           f"(1/{s.fall_confirm_samples} consecutive samples).",
                                 "source": "dashboard (model)"})
        elif self.episode is not None:
            if res.fall_count == 0:
                if self.episode["max"] < s.fall_confirm_samples:
                    self._emit("event", {"t": now, "type": "fall", "severity": "info",
                                         "title": "Suspected fall cleared",
                                         "detail": f"Reached {self.episode['max']}/{s.fall_confirm_samples} "
                                                   f"consecutive fall samples; no alert sent.",
                                         "source": "dashboard (model)"})
                self.episode = None
            else:
                self.episode["max"] = max(self.episode["max"], res.fall_count)
        self._broadcast({"type": "sample", "sample": sample})

    def _emulator_feed(self, kind, status, now):
        self._console("rx", f"[demo emulator] {'EVENT' if kind == 'fw_event' else 'GSMLOG'},{status}", now)
        self.tracker.on_rx(kind, status, "", now)

    # ---------------------------------------------------------------- ticker
    def _ticker(self):
        last_status = 0.0
        while not self._ticker_stop.is_set():
            now = time.time()
            with self.lock:
                if self.emulator:
                    self.emulator.tick(now)
                self.tracker.tick(now)
                lsa = self.stats.get("last_sample_at")
                if (self.mode != "idle" and lsa and not self.stream_stalled
                        and now - lsa >= self.settings.stale_seconds):
                    self.stream_stalled = True
                    busy = self.tracker.current is not None and self.tracker.current.phase == "sms"
                    self._emit("event", {"t": now, "type": "sensor", "severity": "warning",
                                         "title": "Sensor data stream stopped",
                                         "detail": (f"No valid MLDATA for {self.settings.stale_seconds:.0f} s. "
                                                    + ("The firmware is busy sending SMS (loop blocked)."
                                                       if busy else "predict_live.py logic sends PRED,CHECK,0.00 to the board.")),
                                         "source": "dashboard"})
                if self.tracker.current is not None and self.tracker.current.phase != "closed":
                    self._broadcast({"type": "tick", "server_time": now})
            if now - last_status >= 1.0:
                last_status = now
                self.push_status()
            time.sleep(0.2)

    # ------------------------------------------------------------- controls
    def stop_worker(self, reason="Stopped by user"):
        w = self.worker
        if w is not None:
            w.stop()
            w.join(timeout=5)
        with self.lock:
            self.worker = None
            if self.recorder:
                self.recorder.close()
                self.recorder = None
            if self.mode != "idle":
                self.event("system", "info", f"{'Live monitoring' if self.mode == 'live' else 'Demo replay'} stopped", reason)
            self._reset("idle", {})
        self._broadcast(self.snapshot())

    def start_worker(self, mode, source, worker_factory):
        self.stop_worker("Switching source")
        with self.lock:
            self._reset(mode, source)
            if mode == "live" and self.record_live:
                self.recorder = Recorder({"port": source.get("port"), "baud": source.get("baud"),
                                          "settings": self.settings.as_dict()})
            self.worker = worker_factory(self)
        self._broadcast(self.snapshot())
        self.worker.start()

    def set_serial_state(self, state, error=None):
        with self.lock:
            prev = self.serial["state"]
            self.serial["state"] = state
            self.serial["error"] = error
            if state == "connected":
                self.serial["connected_at"] = time.time()
            if state != prev:
                if state == "connected":
                    self.event("system", "info", f"Serial connected: {self.serial['port']} @ {self.serial['baud']}",
                               "The dashboard now owns this port. Do not open Arduino Serial Monitor or predict_live.py.")
                elif state == "error":
                    self.event("system", "critical", "Serial error", error or "")
                elif state == "disconnected" and prev == "connected":
                    self.event("sensor", "critical", "Serial port disconnected", error or "")
        self.push_status()

    def demo_button(self):
        with self.lock:
            if self.emulator is None:
                return False
            ok = self.emulator.press_button(time.time())
        return ok

    def events_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "time", "mode", "type", "severity", "title", "detail", "source"])
        with self.lock:
            for e in self.events:
                w.writerow([e["id"], datetime.fromtimestamp(e["t"]).isoformat(timespec="seconds"), e["mode"],
                            e["type"], e["severity"], e["title"], e.get("detail", ""), e.get("source", "")])
        return buf.getvalue()
