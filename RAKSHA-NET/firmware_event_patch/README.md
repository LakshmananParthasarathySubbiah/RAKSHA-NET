# Optional firmware event patch

`falldeetectml_events/falldeetectml_events.ino` is a copy of `falldeetectml/falldeetectml.ino` with these additions only (full diff in `event_patch.diff`):

1. A helper `reportEvent(kind, status)` that prints `kind,status` on the USB serial port.
2. `EVENT,EVENT_REPORTING_ON` printed once at start-up.
3. `EVENT,<status>` printed at the start of `logFallEvent()`: `FALL_DETECTED_WAITING`, `FALL_CANCELLED_BUTTON`, `FALL_CONFIRMED_SMS_SENT`.
4. `GSMLOG,<status>` printed at the start of `saveGSMLog()`: `<number>_SMS_SENT`, `<number>_SMS_FAILED`, `FALL_SMS_NOT_SENT`, `GSM_INIT_FAILED`.

The status strings are the ones the firmware already writes to the MicroSD card. The prints are placed before the `sdReady` check, so they appear even without a card.

Nothing else changes: same pins, timings, prediction handling, LCD pages and SMS text. `predict_live.py`, `collect_data.py` and `serial_debug.py` ignore the new lines.

## Why use it

The original firmware does not print alert start, button cancellation or "GSM not ready" on serial, so the dashboard has to infer those outcomes. With the patch, the dashboard shows them as reported by the firmware.

## Status

Both the original sketch and this patched copy were compiled with arduino-cli for board `esp32:esp32:esp32s3` (ESP32 Arduino core 2.0.9, LiquidCrystal_I2C library). Both compiled without errors:

| Sketch | Program storage | Global variables |
|---|---|---|
| falldeetectml.ino (original) | 941,873 bytes | 44,680 bytes |
| falldeetectml_events.ino (patch) | 942,013 bytes | 44,680 bytes |

It has not been uploaded to a board. Upload it with the same board, partition and library settings you use for the original sketch, and check that `EVENT,EVENT_REPORTING_ON` appears in the dashboard console after a reset.
