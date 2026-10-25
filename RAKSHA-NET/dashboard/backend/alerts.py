"""Fall alert tracking.

The firmware keeps the alert state (12 s cancel window, buzzer, button,
SMS). The unmodified firmware does not print anything on serial when an
alert starts, is cancelled or ends. What the dashboard can see is:

  * the PRED commands it sends to the board (it knows exactly when the board
    receives PRED,FALL and can apply the same rule as handlePredictionCommand()),
  * "Sending GSM test SMS..." and the SIM800L reply that the firmware prints
    for each SMS attempt,
  * the sensor stream pausing while the firmware is busy sending SMS.

AlertTracker mirrors the firmware rules from that information. Anything it
cannot observe is marked as inferred or unknown, never as a fact. If the
optional event patch is flashed (firmware_event_patch/), the firmware prints
EVENT,<status> and GSMLOG,<status> lines and those are used instead.

FirmwareEmulator is used only in demo mode, where no board is attached. It
applies the same firmware rules so the alert flow can be shown, and it never
reports an SMS.
"""
import itertools
import re

from .lines import mask_number

FALL_CONFIRM_WINDOW_S = 12.0  # FALL_CONFIRM_WINDOW_MS in the firmware
INFER_GRACE_S = 4.0  # wait after the window before inferring an outcome
SMS_ATTEMPT_TIMEOUT_S = 30.0  # firmware waits up to 5 s + 20 s per attempt
EVENT_WAIT_LIMIT_S = 60.0

_ids = itertools.count(1)


def sms_status_from_reply(lines):
    """Same test as isSMSSent() in the firmware, plus a readable explanation."""
    text = "\n".join(lines)
    if "+CMGS:" in text and "OK" in text and "ERROR" not in text:
        return "accepted", (
            "Modem replied +CMGS and OK: the SIM800L accepted the message for sending. "
            "Delivery to the phone is not reported by the firmware."
        )
    if "ERROR" in text:
        err = next((ln for ln in lines if "ERROR" in ln), "ERROR")
        return "error", f"Modem replied with an error: {err}"
    if not lines:
        return "no_reply", (
            "No modem reply was printed. The firmware prints nothing when the '>' prompt is "
            "missing, so this attempt is not confirmed."
        )
    return "no_confirmation", "Modem reply did not contain +CMGS and OK. Not confirmed."


class SmsAttempt:
    def __init__(self, index, started_at):
        self.index = index
        self.started_at = started_at
        self.lines = []
        self.finished_at = None
        self.status = "pending"
        self.status_text = "Waiting for modem reply"
        self.firmware_status = None

    def finish(self, now):
        if self.finished_at is None:
            self.finished_at = now
            self.status, self.status_text = sms_status_from_reply(self.lines)

    def to_dict(self):
        return {
            "index": self.index,
            "recipient": f"Recipient {self.index}",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "reply": [mask_number(x) for x in self.lines],
            "status": self.status,
            "status_text": self.status_text,
            "firmware_status": mask_number(self.firmware_status) if self.firmware_status else None,
        }


class Alert:
    def __init__(self, started_at, confidence, room, rssi, source, demo):
        self.id = next(_ids)
        self.started_at = started_at
        self.start_known = started_at is not None
        self.confidence = confidence
        self.room = room
        self.rssi = rssi
        self.start_source = source
        self.start_confirmed_by_firmware = False
        self.demo = demo
        self.phase = "window"  # window -> sms -> closed
        self.window_end = (started_at + FALL_CONFIRM_WINDOW_S) if started_at is not None else None
        self.sms = []
        self.gsm_not_ready = False
        self.demo_no_modem = False
        self.closed_at = None
        self.outcome = None
        self.outcome_text = None
        self.outcome_source = None

    def close(self, now, outcome, text, source):
        if self.phase == "closed":
            return False
        for a in self.sms:
            a.finish(now)
        self.phase = "closed"
        self.closed_at = now
        self.outcome = outcome
        self.outcome_text = text
        self.outcome_source = source
        return True

    def to_dict(self):
        return {
            "id": self.id,
            "started_at": self.started_at,
            "start_known": self.start_known,
            "window_s": FALL_CONFIRM_WINDOW_S,
            "window_end": self.window_end,
            "confidence": self.confidence,
            "room": self.room,
            "rssi": self.rssi,
            "start_source": self.start_source,
            "start_confirmed_by_firmware": self.start_confirmed_by_firmware,
            "demo": self.demo,
            "phase": self.phase,
            "sms": [a.to_dict() for a in self.sms],
            "gsm_not_ready": self.gsm_not_ready,
            "closed_at": self.closed_at,
            "outcome": self.outcome,
            "outcome_text": self.outcome_text,
            "outcome_source": self.outcome_source,
        }


def _parse_pred(cmd):
    parts = cmd.strip().split(",")
    if len(parts) < 3 or parts[0] != "PRED":
        return None, None
    try:
        conf = float(parts[2])
    except ValueError:
        conf = None
    return parts[1].upper(), conf


class AlertTracker:
    """Follows the firmware alert state. `emit(kind, payload)` sends updates to the hub."""

    def __init__(self, emit, board, demo=False):
        self.emit = emit
        self.board = board  # dict shared with the hub (gsm, event_reporting, ...)
        self.demo = demo
        self.board_last_pred_fall = False  # mirrors lastPredictionWasFall
        self.current = None
        self.history = []
        self.collecting = None  # SmsAttempt receiving reply lines
        self.last_sample_at = 0.0

    # ----- helpers -------------------------------------------------------
    def _publish(self, alert):
        self.emit("alert", alert.to_dict())

    def _event(self, etype, severity, title, detail, source, t):
        self.emit("event", {"t": t, "type": etype, "severity": severity, "title": title,
                            "detail": detail, "source": source})

    def _start(self, now, confidence, room, rssi, source):
        alert = Alert(now, confidence, room, rssi, source, self.demo)
        self.current = alert
        self.history.append(alert)
        self.history = self.history[-200:]
        conf_txt = f"{confidence:.2f}" if confidence is not None else "n/a"
        self._event("fall", "critical", "Fall alert started on board",
                    f"PRED,FALL sent (confidence {conf_txt}). Board starts buzzer and "
                    f"{int(FALL_CONFIRM_WINDOW_S)} s cancel window. Room: {room or 'unknown'}.",
                    source, now)
        self._publish(alert)
        return alert

    def _close(self, now, outcome, text, source, etype="fall", severity="warning", title=None):
        alert = self.current
        if alert is None:
            return
        self._finish_collecting(now)
        if alert.close(now, outcome, text, source):
            self._event(etype, severity, title or "Fall alert closed", text, source, now)
            self._publish(alert)
        self.current = None

    def _finish_collecting(self, now):
        if self.collecting is not None:
            att = self.collecting
            att.finish(now)
            self.collecting = None
            sev = "info" if att.status == "accepted" else "warning"
            self._event("gsm", sev, f"SMS attempt {att.index}: {att.status.replace('_', ' ')}",
                        att.status_text + (" Reply: " + " | ".join(att.lines) if att.lines and att.status != "error" else ""),
                        "serial (modem reply)", now)
            if self.current:
                self._publish(self.current)

    # ----- inputs --------------------------------------------------------
    def on_tx(self, cmd, now, room=None, rssi=None):
        status, conf = _parse_pred(cmd)
        if status is None:
            return
        if status == "FALL":
            if not self.board_last_pred_fall:
                a = self.current
                if a is not None and a.phase == "sms":
                    # The board's alert is no longer active after the window, so a new
                    # PRED,FALL starts a new alert. Resolve the old one first.
                    if a.sms:
                        self._close_after_sms(now, "serial (SMS replies)")
                    else:
                        self._infer_close(now)
                if self.current is None or self.current.phase == "closed":
                    self._start(now, conf, room, rssi, "dashboard (PRED,FALL sent to board)")
            self.board_last_pred_fall = True
        else:
            self.board_last_pred_fall = False

    def on_sample(self, now):
        """Called for every MLDATA / sensor text line (sensor stream is running)."""
        self.last_sample_at = now
        if self.collecting is not None:
            self._finish_collecting(now)
        a = self.current
        if a and a.phase == "sms" and a.sms and all(x.finished_at for x in a.sms):
            if not self.board.get("event_reporting"):
                self._close_after_sms(now, "serial (SMS replies, sensor stream resumed)")

    def _close_after_sms(self, now, source):
        a = self.current
        parts = [f"Recipient {x.index}: {x.status.replace('_', ' ')}" for x in a.sms]
        accepted = sum(1 for x in a.sms if x.status == "accepted")
        text = (f"Cancel window elapsed with no cancellation. Firmware attempted {len(a.sms)} SMS "
                f"({'; '.join(parts)}). {accepted} accepted by the modem; delivery to phones is not reported.")
        self._close(now, "confirmed_sms_attempted", text, source, severity="critical",
                    title="Fall confirmed: emergency SMS attempted")

    def on_rx(self, kind, data, line, now):
        if kind == "sms_start":
            a = self.current
            if a is None or a.phase == "closed":
                a = Alert(None, None, None, None, "serial (SMS seen without tracked start)", self.demo)
                self.current = a
                self.history.append(a)
            self._finish_collecting(now)
            a.phase = "sms"
            att = SmsAttempt(len(a.sms) + 1, now)
            a.sms.append(att)
            self.collecting = att
            self._event("gsm", "info", f"SMS attempt {att.index} started",
                        "Firmware printed 'Sending GSM test SMS...'", "serial", now)
            self._publish(a)
            return
        if kind in ("other",) and self.collecting is not None:
            self.collecting.lines.append(line)
            if self.current:
                self._publish(self.current)
            return
        if kind == "fw_event":
            self._on_fw_event(data, now, "firmware EVENT line" if not self.demo else "demo firmware emulator")
            return
        if kind == "fw_gsmlog":
            self._on_fw_gsmlog(data, now, "firmware GSMLOG line" if not self.demo else "demo firmware emulator")
            return

    def _on_fw_event(self, status, now, source):
        a = self.current
        if status == "FALL_DETECTED_WAITING":
            if a is None or a.phase == "closed":
                a = self._start(now, None, None, None, source)
            a.start_confirmed_by_firmware = True
            self._publish(a)
        elif status == "FALL_CANCELLED_BUTTON":
            if a is None:
                return
            self._close(now, "cancelled", "Alert cancelled with the button during the cancel window. No SMS sent.",
                        source, etype="cancel", severity="info", title="Fall alert cancelled (button)")
        elif status == "FALL_CONFIRMED_SMS_SENT":
            if a is None:
                return
            self._finish_collecting(now)
            if a.demo_no_modem:
                self._close(now, "demo_no_modem",
                            "Cancel window elapsed with no cancellation. Demo mode: no GSM modem, no SMS attempted.",
                            source, severity="critical", title="Fall confirmed (demo, no SMS)")
            elif a.sms:
                self._close_after_sms(now, source)
            elif a.gsm_not_ready:
                self._close(now, "confirmed_no_gsm",
                            "Cancel window elapsed with no cancellation. GSM was not ready, so no SMS was attempted.",
                            source, severity="critical", title="Fall confirmed: SMS not sent (GSM not ready)")
            else:
                self._close(now, "window_elapsed",
                            "Cancel window elapsed. Firmware logged FALL_CONFIRMED_SMS_SENT, but no SMS attempt "
                            "was seen on serial.", source, severity="critical", title="Fall confirmed")

    def _on_fw_gsmlog(self, status, now, source):
        a = self.current
        if status == "FALL_SMS_NOT_SENT":
            if a:
                a.gsm_not_ready = True
                self._publish(a)
            return
        if status == "DEMO_NO_MODEM":
            if a:
                a.demo_no_modem = True
                self._publish(a)
            return
        m = re.match(r"^(.*)_(SMS_SENT|SMS_FAILED)$", status)
        if m and a:
            target = self.collecting or (a.sms[-1] if a.sms else None)
            if target is not None:
                target.firmware_status = status
                self._finish_collecting(now)
                self._publish(a)

    def tick(self, now):
        a = self.current
        if a is None:
            return
        if self.collecting is not None and now - self.collecting.started_at > SMS_ATTEMPT_TIMEOUT_S:
            self._finish_collecting(now)
        if a.phase == "window" and a.window_end is not None and now >= a.window_end:
            a.phase = "sms"
            self._publish(a)
            return
        if a.phase != "sms" or a.window_end is None:
            return
        if self.board.get("event_reporting"):
            if now - a.window_end > EVENT_WAIT_LIMIT_S and not a.sms:
                self._close(now, "unknown", "No firmware event received after the cancel window.",
                            "dashboard (timeout)", severity="warning", title="Fall alert outcome unknown")
            return
        # Unmodified firmware: infer only once the sensor stream is running again
        # after the window and no SMS attempt has been printed.
        if a.sms or now < a.window_end + INFER_GRACE_S or self.last_sample_at <= a.window_end + 0.5:
            return
        self._infer_close(now)

    def _infer_close(self, now):
        gsm = self.board.get("gsm")
        if gsm == "ready":
            self._close(now, "cancelled_inferred",
                        "No SMS activity on serial after the 12 s window while GSM was ready at boot. "
                        "The alert was most likely cancelled with the button. Inferred: the current "
                        "firmware does not print cancellations.",
                        "inferred by dashboard", etype="cancel", severity="info",
                        title="Fall alert cancelled (inferred)")
        elif gsm == "fail":
            self._close(now, "no_sms_gsm_fail",
                        "No SMS activity after the 12 s window. GSM failed at boot, so either the alert was "
                        "cancelled or the firmware skipped SMS (GSM not ready). The current firmware does not "
                        "print which.", "inferred by dashboard", severity="warning",
                        title="Fall alert ended: outcome not reported")
        else:
            self._close(now, "unknown",
                        "No SMS activity after the 12 s window. GSM status at boot was not seen, so the "
                        "dashboard cannot tell if the alert was cancelled or SMS was skipped.",
                        "inferred by dashboard", severity="warning", title="Fall alert ended: outcome not reported")

    def snapshot(self):
        return {
            "current": self.current.to_dict() if self.current else None,
            "history": [a.to_dict() for a in self.history[-50:]],
            "board_last_pred_fall": self.board_last_pred_fall,
        }


class FirmwareEmulator:
    """Demo only: the firmware's handlePredictionCommand / handleFallAlert rules."""

    def __init__(self, feed):
        self.feed = feed  # feed(kind, status, now) -> pushes fw_event / fw_gsmlog into the tracker
        self.last_pred_fall = False
        self.active = False
        self.started_at = 0.0

    def on_tx(self, cmd, now):
        status, _ = _parse_pred(cmd)
        if status == "FALL":
            if not self.last_pred_fall and not self.active:
                self.active = True
                self.started_at = now
                self.feed("fw_event", "FALL_DETECTED_WAITING", now)
            self.last_pred_fall = True
        elif status is not None:
            self.last_pred_fall = False

    def press_button(self, now):
        if self.active:
            self.active = False
            self.feed("fw_event", "FALL_CANCELLED_BUTTON", now)
            return True
        return False

    def tick(self, now):
        if self.active and now - self.started_at >= FALL_CONFIRM_WINDOW_S:
            self.active = False
            self.feed("fw_gsmlog", "DEMO_NO_MODEM", now)
            self.feed("fw_event", "FALL_CONFIRMED_SMS_SENT", now)
