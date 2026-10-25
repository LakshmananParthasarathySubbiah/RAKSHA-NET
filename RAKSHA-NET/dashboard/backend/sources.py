"""Data sources: the live serial port and the demo replays."""
import json
import os
import threading
import time

import pandas as pd

from .engine import PredictionEngine
from .paths import DATASET_FILE, RECORDINGS_DIR


def list_serial_ports():
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    out = []
    for p in sorted(list_ports.comports(), key=lambda x: x.device):
        out.append({"device": p.device, "description": p.description or "", "hwid": p.hwid or ""})
    return out


class _Worker(threading.Thread):
    def __init__(self, hub, name):
        super().__init__(name=name, daemon=True)
        self.hub = hub
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()

    @property
    def stopping(self):
        return self._stop_evt.is_set()


class SerialWorker(_Worker):
    """The only code that opens the serial port. Reads with the same settings as predict_live.py."""

    def __init__(self, hub, port, baud):
        super().__init__(hub, "serial-worker")
        self.port = port
        self.baud = baud

    def run(self):
        import serial

        hub = self.hub
        hub.set_serial_state("connecting")
        kwargs = {}
        if os.name != "nt":
            kwargs["exclusive"] = True  # Windows ports are exclusive already
        try:
            ser = serial.Serial(self.port, self.baud, timeout=1, **kwargs)
        except (serial.SerialException, OSError, ValueError) as exc:
            msg = str(exc)
            if "denied" in msg.lower() or "busy" in msg.lower() or "PermissionError" in msg:
                msg += (" | The port is probably open in another program. Close Arduino Serial Monitor, "
                        "Serial Plotter, predict_live.py, collect_data.py or another dashboard window.")
            hub.set_serial_state("error", msg)
            return
        hub.set_serial_state("connected")
        error = None
        try:
            while not self.stopping:
                now = time.time()
                try:
                    raw = ser.readline()
                except (serial.SerialException, OSError) as exc:
                    error = f"Read failed: {exc}. The board may have been unplugged or reset."
                    break
                line = raw.decode(errors="ignore").strip()
                out = hub.process_rx(line, now)
                if out is not None:
                    try:
                        ser.write(out)
                    except (serial.SerialException, OSError) as exc:
                        error = f"Write failed: {exc}"
                        break
        finally:
            # Same as the Ctrl+C handler in predict_live.py
            final = PredictionEngine.shutdown_command(hub.settings)
            if final and error is None:
                try:
                    ser.write(final)
                    hub._console("tx", final.decode().strip(), time.time())
                except Exception:
                    pass
            try:
                ser.close()
            except Exception:
                pass
            hub.set_serial_state("disconnected", error)


def dataset_playlist(df, straight_block=40, fall_block=25):
    """Alternate blocks of consecutive recorded rows: straight, fall, straight, ...

    Rows keep the order in which collect_data.py recorded them.
    """
    s_idx = df.index[df["label"] == "straight"].tolist()
    f_idx = df.index[df["label"] == "fall"].tolist()
    order, i, j = [], 0, 0
    while i < len(s_idx) or j < len(f_idx):
        order.extend(s_idx[i:i + straight_block])
        i += straight_block
        order.extend(f_idx[j:j + fall_block])
        j += fall_block
    return order


class DatasetReplayWorker(_Worker):
    """Demo: feeds rows of fall_dataset.csv to the same pipeline as MLDATA lines, one per second.

    Room and GSM data do not exist in the dataset, so none is shown.
    """

    def __init__(self, hub, interval=1.0):
        super().__init__(hub, "dataset-replay")
        self.interval = interval

    def run(self):
        df = pd.read_csv(DATASET_FILE)
        order = dataset_playlist(df)
        hub = self.hub
        hub.event("demo", "info", "Demo replay started",
                  f"Replaying {len(df)} recorded rows from {DATASET_FILE.name} at 1 row/s "
                  "(about the firmware's MLDATA rate). Most rows were used to train the model.",
                  "demo")
        pos = 0
        next_t = time.time()
        while not self.stopping:
            r = df.loc[order[pos]]
            line = (f"MLDATA,{r.ax:.4f},{r.ay:.4f},{r.az:.4f},{r.gx:.4f},{r.gy:.4f},{r.gz:.4f}")
            hub.process_rx(line, time.time())
            with hub.lock:
                hub.source["position"] = pos + 1
                hub.source["total"] = len(order)
                hub.source["row_index"] = int(order[pos])
                hub.source["row_label"] = str(r.label)
            pos += 1
            if pos >= len(order):
                pos = 0
                hub.event("demo", "info", "Demo replay restarted from the first row", "", "demo")
            next_t += self.interval
            delay = next_t - time.time()
            if delay > 0:
                self._stop_evt.wait(delay)
            else:
                next_t = time.time()


def read_recording(path):
    header, items = {}, []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "header":
                header = obj
            elif obj.get("dir") == "rx" and isinstance(obj.get("line"), str):
                items.append((float(obj.get("t", 0.0)), obj["line"]))
    return header, items


def list_recordings():
    out = []
    for p in sorted(RECORDINGS_DIR.glob("*.jsonl"), reverse=True):
        try:
            header, items = read_recording(p)
        except Exception:
            continue
        if not items:
            continue
        out.append({"file": p.name, "created": header.get("created_text"), "port": header.get("port"),
                    "lines": len(items), "duration_s": round(items[-1][0] - items[0][0], 1),
                    "size_kb": round(p.stat().st_size / 1024, 1)})
    return out


class RecordingReplayWorker(_Worker):
    """Demo: replays a session recorded by the dashboard in live mode, in real time.

    Received lines are fed to the pipeline with their original spacing. Gaps
    longer than 1 s produce empty reads, as pyserial's timeout=1 would.
    Commands are recomputed by the prediction engine (no board is attached).
    """

    def __init__(self, hub, path):
        super().__init__(hub, "recording-replay")
        self.path = path

    def run(self):
        hub = self.hub
        header, items = read_recording(self.path)
        hub.event("demo", "info", "Recorded session replay started",
                  f"{self.path.name}: {len(items)} lines recorded {header.get('created_text', '?')} "
                  f"on {header.get('port', '?')}.", "demo")
        while not self.stopping:
            start_wall = time.time()
            t_first = items[0][0]
            last_feed = start_wall
            for idx, (t, line) in enumerate(items):
                target = start_wall + (t - t_first)
                while not self.stopping:
                    now = time.time()
                    if now >= target:
                        break
                    if now - last_feed >= 1.0:
                        hub.process_rx("", now)  # readline() timeout
                        last_feed = now
                    self._stop_evt.wait(min(0.05, max(0.0, target - now)))
                if self.stopping:
                    return
                hub.process_rx(line, time.time())
                last_feed = time.time()
                with hub.lock:
                    hub.source["position"] = idx + 1
                    hub.source["total"] = len(items)
            hub.event("demo", "info", "Recorded session replay reached the end; restarting", "", "demo")
            self._stop_evt.wait(2.0)
