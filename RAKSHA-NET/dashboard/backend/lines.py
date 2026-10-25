"""Classify the text lines printed by falldeetectml.ino.

Every pattern here is taken from a Serial print in the firmware. Lines that
match nothing are kept as "other" (for example AT command replies from the
SIM800L, which the firmware echoes with logLine()).
"""
import re

ROOM_RE = re.compile(r"\|\s*Room:\s*(?P<room>\S*)\s+RSSI:\s*(?P<rssi>-?\d+)\s*$")
GSM_BAUD_RE = re.compile(r"^GSM baud detected:\s*(\d+)")

# Exact messages from logLine(...) calls in the firmware.
FIXED = {
    "RAKSHA-NET starting...": ("boot", None),
    "SD card ready.": ("sd_ok", None),
    "SD card mount failed.": ("sd_fail", None),
    "GSM ready.": ("gsm_ready", None),
    "GSM AT failed.": ("gsm_fail", None),
    "GSM AT failed after baud detect.": ("gsm_fail", None),
    "MPU6500 ready.": ("mpu_ok", None),
    "MPU6500 not found.": ("mpu_missing", None),
    "MPU6500 read failed.": ("mpu_read_fail", None),
    "Beacon scanner started on core 0.": ("beacon_started", None),
    "Sending GSM test SMS...": ("sms_start", None),
    "Failed to open ai_fall_log.csv.": ("sd_write_fail", "ai_fall_log.csv"),
    "Failed to open gsm_log.csv.": ("sd_write_fail", "gsm_log.csv"),
    "Failed to open fall_log.csv.": ("sd_write_fail", "fall_log.csv"),
}

# Hint lines printed after a failure; shown in the console, not logged as events.
HINT_PREFIXES = (
    "Check CS=",
    "Check SDA=",
    "If wiring is correct",
    "Sample fall_log.csv",
)


def classify(line: str):
    """Return (kind, data) for one stripped serial line."""
    if not line:
        return "empty", None
    if line.startswith("MLDATA,"):
        return "mldata", None
    if line.startswith("ACC X:"):
        m = ROOM_RE.search(line)
        if m:
            return "sensor_text", {"room": m.group("room"), "rssi": int(m.group("rssi"))}
        return "sensor_text", None
    # Lines added by the optional firmware event patch (firmware_event_patch/).
    if line.startswith("EVENT,"):
        return "fw_event", line.split(",", 1)[1].strip()
    if line.startswith("GSMLOG,"):
        return "fw_gsmlog", line.split(",", 1)[1].strip()
    if line in FIXED:
        return FIXED[line]
    m = GSM_BAUD_RE.match(line)
    if m:
        return "gsm_baud", int(m.group(1))
    if line.startswith(HINT_PREFIXES):
        return "hint", None
    return "other", None


def mask_number(text: str) -> str:
    """Hide all but the last 4 digits of phone numbers (10+ digits)."""
    return re.sub(r"\+?\d{6,}(\d{4})", lambda m: "******" + m.group(1), text)
