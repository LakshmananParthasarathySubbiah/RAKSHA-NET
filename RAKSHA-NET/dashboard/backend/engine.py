"""Prediction engine.

This is the loop body of predict_live.py main(), split into a class so the
dashboard can call it once per serial line. The order of checks, the
counters, the thresholds and the commands written to the board are the same
as in the original script. tests/test_engine_equivalence.py runs the
original main() and this class on the same input and compares every byte
written to the serial port.

One addition: a malformed MLDATA line (for example a garbled number) makes
float() raise in parse_ml_line(). The original script would stop with an
exception. Here the line is counted as malformed and treated like any other
non-sample line, so the dashboard keeps running.
"""
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .legacy import FEATURES, PREDICT_LIVE_DEFAULTS, board_command, has_real_sensor_data, parse_ml_line


@dataclass
class EngineSettings:
    alert_threshold: float = PREDICT_LIVE_DEFAULTS["alert_threshold"]
    send_interval: float = PREDICT_LIVE_DEFAULTS["send_interval"]
    fall_confirm_samples: int = PREDICT_LIVE_DEFAULTS["fall_confirm_samples"]
    min_acc_mag: float = PREDICT_LIVE_DEFAULTS["min_acc_mag"]
    stale_seconds: float = PREDICT_LIVE_DEFAULTS["stale_seconds"]
    no_board_alert: bool = PREDICT_LIVE_DEFAULTS["no_board_alert"]

    def as_dict(self):
        return dict(self.__dict__)


@dataclass
class EngineResult:
    kind: str = "none"  # none | stale | invalid | sample
    row: Optional[list] = None
    prediction: Optional[str] = None
    probability: Optional[float] = None  # max class probability, as in predict_live.py
    p_fall: Optional[float] = None  # probability of the "fall" class from the same predict_proba call
    fall_count: int = 0
    command: Optional[str] = None  # command computed for this sample
    write: Optional[bytes] = None  # bytes to write to the board (None = nothing written)
    malformed: bool = False
    extra: dict = field(default_factory=dict)


class PredictionEngine:
    def __init__(self, model, settings: EngineSettings):
        self.model = model
        self.settings = settings
        self.last_sent_at = 0.0
        self.last_data_at = 0.0
        self.consecutive_fall_count = 0
        classes = [str(c).strip().lower() for c in getattr(model, "classes_", [])]
        self._fall_index = classes.index("fall") if "fall" in classes else None

    def process(self, raw_line: str, now: float) -> EngineResult:
        """Handle one line from ser.readline() ('' means the read timed out).

        `now` must be taken before the read, as predict_live.py does.
        """
        s = self.settings
        result = EngineResult()

        row = None
        if raw_line.startswith("MLDATA,"):
            try:
                row = parse_ml_line(raw_line)
            except ValueError:
                row = None
                result.malformed = True

        if row is None:
            if (
                self.last_data_at
                and now - self.last_data_at >= s.stale_seconds
                and not s.no_board_alert
                and now - self.last_sent_at >= s.send_interval
            ):
                result.kind = "stale"
                result.command = "PRED,CHECK,0.00\n"
                result.write = b"PRED,CHECK,0.00\n"
                self.last_sent_at = now
                self.consecutive_fall_count = 0
            return result

        if not has_real_sensor_data(row, s.min_acc_mag):
            self.consecutive_fall_count = 0
            result.kind = "invalid"
            result.row = row
            return result

        self.last_data_at = now

        sample = pd.DataFrame([row], columns=FEATURES)
        prediction = self.model.predict(sample)[0]
        proba = self.model.predict_proba(sample)[0]
        probability = max(proba)
        normalized_prediction = str(prediction).strip().lower()

        if normalized_prediction == "fall" and probability >= s.alert_threshold:
            self.consecutive_fall_count += 1
        else:
            self.consecutive_fall_count = 0

        if self.consecutive_fall_count >= s.fall_confirm_samples:
            command = board_command(prediction, probability, s.alert_threshold)
        elif normalized_prediction == "straight":
            command = board_command(prediction, probability, s.alert_threshold)
        else:
            command = f"PRED,CHECK,{probability:.2f}\n"

        if not s.no_board_alert and (command.startswith("PRED,FALL") or now - self.last_sent_at >= s.send_interval):
            result.write = command.encode()
            self.last_sent_at = now

        result.kind = "sample"
        result.row = row
        result.prediction = normalized_prediction
        result.probability = float(probability)
        result.p_fall = float(proba[self._fall_index]) if self._fall_index is not None else None
        result.fall_count = self.consecutive_fall_count
        result.command = command
        return result

    @staticmethod
    def shutdown_command(settings: EngineSettings) -> Optional[bytes]:
        """What predict_live.py writes when it is stopped with Ctrl+C."""
        return None if settings.no_board_alert else b"PRED,NORMAL,0.00\n"
