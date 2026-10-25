# RAKSHA-NET monitoring dashboard

A local web dashboard for the RAKSHA-NET fall detection project. It shows what currently appears in the Python terminal and the Arduino Serial Monitor: MPU6500 readings, the Random Forest prediction, the fall alert sequence, SMS attempts, the BLE room and the logged events.

The dashboard runs on the laptop that is connected to the ESP32-S3 by USB. It replaces `predict_live.py` while it is running: it opens the serial port, runs the same prediction code and sends the same `PRED,...` commands to the board. The browser only displays what the Python backend reports.

## Contents

```
RAKSHA-NET/
  falldeetectml/falldeetectml.ino      original firmware (unchanged)
  ml_laptop/                           original scripts, dataset and model (unchanged, used by the dashboard)
  firmware_event_patch/                optional firmware copy that prints alert events on serial
  dashboard/
    run_dashboard.py                   starts the dashboard
    setup_windows.bat                  one-time install on Windows
    start_dashboard.bat                start (live or demo chosen in the browser)
    start_demo.bat                     start directly in demo mode
    run_tests.bat                      run the automated tests on Windows
    requirements.txt                   Python packages
    backend/                           Python backend (FastAPI)
      legacy.py                        imports parse_ml_line, board_command, has_real_sensor_data from ml_laptop
      engine.py                        loop body of predict_live.py, one serial line at a time
      lines.py                         recognises every line format printed by the firmware
      alerts.py                        follows the firmware's alert and SMS sequence
      hub.py                           shared state, events, recording
      sources.py                       serial port reader and the two demo replays
      ml_report.py                     confusion matrix, metrics and feature importance from the real files
      app.py                           HTTP and WebSocket API
    static/                            browser page (HTML, CSS, JavaScript, Chart.js and fonts bundled, works offline)
    tests/                             automated tests and a test-only fake ESP32
    recordings/                        live sessions saved for later replay (created automatically)
    logs/                              event log, one JSON line per event
    sd_logs/                           put CSV files copied from the MicroSD card here
```

## Requirements

* Windows 10 or 11 laptop with the ESP32-S3 connected by USB.
* Python 3.11, 3.12, 3.13 or 3.14 from python.org. Python 3.10 is not supported because the model was saved with scikit-learn 1.8.0, which needs Python 3.11 or newer.
* Internet access once, for the package installation. After that the dashboard works offline.
* Any current browser (Chrome, Edge, Firefox).

## Windows installation (once)

1. Install Python from python.org. During installation, tick "Add python.exe to PATH".
2. Copy the whole `RAKSHA-NET` folder to the laptop. Keep the folder structure: the dashboard reads the model and dataset from `..\ml_laptop`.
3. Open `RAKSHA-NET\dashboard` and double-click `setup_windows.bat`. It creates a `.venv` folder and installs the packages in `requirements.txt`. This takes a few minutes.

Manual alternative from a Command Prompt in the `dashboard` folder:

```
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Starting the dashboard

### With the hardware

1. Flash `falldeetectml.ino` (or the optional event patch, see below) and plug the ESP32-S3 into the laptop.
2. Close Arduino Serial Monitor, Serial Plotter and any running `predict_live.py`, `collect_data.py` or `serial_debug.py`. Only one program can hold the COM port.
3. Double-click `start_dashboard.bat`. A console window opens, and the browser opens at http://127.0.0.1:8000. Keep the console window open while you use the dashboard.
4. Press **Data source**, pick the COM port (for example COM6), keep 115200 baud and press **Connect**.
5. To stop, press **Stop current source** in the dashboard, or close the console window. On stop the dashboard sends `PRED,NORMAL,0.00` to the board, as `predict_live.py` does on Ctrl+C.

To connect straight away without the browser form, run from the `dashboard` folder:

```
start_dashboard.bat --serial-port COM6
start_dashboard.bat --serial-port COM6 --fall-confirm-samples 5
```

The prediction options have the same names and defaults as `predict_live.py`: `--alert-threshold 0.70`, `--fall-confirm-samples 3`, `--send-interval 1.0`, `--min-acc-mag 0.20`, `--stale-seconds 3.0`, `--no-board-alert`. They can also be changed in the Data source panel before connecting.

### Without the hardware (demo mode)

Double-click `start_demo.bat`, or choose **Demo replay** in the Data source panel. Demo mode is marked with a striped yellow DEMO banner on every page.

| Demo source | What it replays | What it does not have |
|---|---|---|
| `fall_dataset.csv` | Recorded accelerometer and gyroscope rows, in recorded order, one per second, through the same prediction code. The board's 12 s cancel window is simulated so the alert sequence can be shown, and an on-screen button stands in for the device button. | No room, no GSM, no SMS. Most rows were used to train the model, so this demonstrates the pipeline, not accuracy. |
| A recorded live session | Every line received from the ESP32 during an earlier live connection, with its original timing, including room and GSM lines. | Nothing is sent to any board. Predictions are recomputed with the current settings. |

Live sessions are recorded automatically to `recordings/` (untick "Record the session" to turn this off). Record a session with the hardware once, and it can be replayed later in a room without the hardware.

## Sections

**Live monitoring**: acceleration X, Y, Z and |a| in g, rotation X, Y, Z and |ω| in dps, the model class and confidence for each sample, P(fall) against the threshold, the count of consecutive fall samples, the last command sent to the board, the serial connection state and sample rate, the board's start-up report (SD card, GSM, MPU6500, BLE scanner), 90-second charts and a serial console with received and sent lines.

**Fall detection**: the five stages (normal monitoring, suspected fall, 12 s cancel window, confirmed fall, emergency SMS) with the current stage highlighted, a countdown during the cancel window, each SMS attempt with the modem's reply, the decision rules in use and the alert history.

**Room localization**: the room reported by the firmware (nearest of ROOM_Bedroom, ROOM_Hallroom, ROOM_Washroom above −95 dBm, or Outside_Range), its RSSI, a schematic of the three rooms, and RSSI history by room. The schematic is not to scale. The firmware reports only the strongest beacon, so the position is room-level only.

**Machine learning**: computed by the dashboard at start-up from `fall_dataset.csv` and `fall_random_forest.joblib`. It shows the dataset distribution, the Random Forest settings, and the confusion matrix, accuracy, precision, recall and F1-score of the saved model on the same 25% test split that `train_random_forest.py` makes. It also shows feature importance and a check that retraining with the script's settings reproduces the saved model. Nothing on this page is typed in by hand. The hardware trial results area is a placeholder until real trials are done.

**Event history**: fall, cancellation, sensor, GSM, room, SD card and system events, with filters, search and CSV export. Events are also appended to `logs/events_YYYYMMDD.jsonl`. This page also has a viewer for CSV files copied from the MicroSD card (`ai_fall_log.csv`, `gsm_log.csv`) and the list of recorded sessions.

## How it works

```
ESP32-S3 firmware                         Laptop: run_dashboard.py (only owner of the COM port)
  MPU6500 -> "ACC X: ... | Room: R RSSI: n" ---> lines.py      room, start-up report, SMS replies
          -> "MLDATA,ax,ay,az,gx,gy,gz"  ---> engine.py     parse_ml_line + RandomForest (same as predict_live.py)
  handlePredictionCommand()  <--- "PRED,FALL|NORMAL|CHECK,conf" ---
  buzzer, 12 s button window, SIM800L SMS ---> alerts.py     alert stage and SMS result
                                              hub.py -> WebSocket -> browser
```

`tests/test_engine_equivalence.py` runs the original `predict_live.main()` and the dashboard engine on the same input and checks that the bytes sent to the board are identical, with default and changed settings.

## What is measured, what is inferred, what is simulated

The dashboard labels the source of each alert outcome.

* **Measured (from serial)**: sensor values, predictions, commands sent, room and RSSI, boot messages, "Sending GSM test SMS..." and the modem reply.
* **SMS result**: an attempt is shown as "Accepted by modem" only if the reply contains `+CMGS` and `OK` and no `ERROR`. This is the same test as `isSMSSent()` in the firmware. It means the SIM800L accepted the message. Delivery to the phone is not reported by the firmware and is never shown as confirmed.
* **Inferred**: the unmodified firmware prints nothing when an alert starts, is cancelled with the button, or ends without SMS. The dashboard knows when it sent `PRED,FALL`, applies the firmware's rule, and infers the rest. If no SMS activity appears after the 12 s window and GSM was ready at boot, it shows "Cancelled (inferred)". If GSM status was not seen, it shows "outcome not reported" rather than guessing.
* **Simulated**: only in demo mode with `fall_dataset.csv`, and always under the DEMO banner.

## Optional firmware event patch

`firmware_event_patch/falldeetectml_events/falldeetectml_events.ino` is the original sketch with one helper function and three print lines added (see `event_patch.diff`). It prints `EVENT,<status>` from `logFallEvent()` and `GSMLOG,<status>` from `saveGSMLog()`, the same status strings the firmware already writes to the SD card. With it, cancellations, "GSM not ready" and the firmware's own SMS result are reported directly instead of inferred. `predict_live.py` and `collect_data.py` ignore these lines, so the existing scripts keep working. Flashing it is optional.

## Tests

From the `dashboard` folder:

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -v tests
```

| Test | What it checks | Result here |
|---|---|---|
| `test_engine_equivalence.py` (16 cases) | Same commands as the original `predict_live.py` for the same serial input, with default and changed options; defaults match the script | Passed |
| `test_api.py` | Pages and API, ML report values from the real files, bad port error, input validation, dataset demo, recorded-session replay, SD CSV import, WebSocket | Passed |
| `test_e2e_serial.py` (3 runs, Linux/macOS only, skipped on Windows) | Full path over a virtual serial port with `tests/fake_esp32.py`: boot report, room changes, an alert cancelled with the button, a confirmed alert with one accepted and one failed SMS, GSM failed at boot, with and without the event patch, and phone numbers masked | Passed |

`tests/fake_esp32.py` is a test tool. Its room names, RSSI values and modem replies are fixed test inputs, not measurements, and it is never used by the dashboard.

## Needs verification with the real hardware

These could not be tested without the board and must be checked on the laptop with the ESP32-S3:

1. The COM port opens on Windows, and a clear message appears if Arduino Serial Monitor already has it open.
2. Whether opening the port restarts the ESP32. If it does not, the start-up report shows "not seen"; press EN/RST once while connected.
3. Live MLDATA rate is about 1 per second (the firmware loop waits 1000 ms between pages), and the charts and readings follow real movement.
4. A real fall with 3 consecutive fall samples sends `PRED,FALL`, the board shows the countdown on the LCD and the buzzer sounds, and the dashboard countdown matches the LCD within about 1 s.
5. Pressing the button (GPIO14 to GND) during the window stops the buzzer. Without the event patch, the dashboard should show "Cancelled (inferred)" about 4 s after the window ends.
6. Without pressing the button, both SMS attempts appear with the SIM800L's real replies, and SMS arrive on both phones. Record actual delivery yourself; the dashboard cannot see it.
7. Room and RSSI match the LCD room page for each beacon, and Outside_Range appears when no beacon is in range.
8. Unplugging the USB cable shows "Serial port disconnected" and the band changes; reconnecting from Data source works.
9. If the event patch is used: it uploads with the same board settings as the original (it was compile-tested only, see firmware_event_patch/README.md), and EVENT/GSMLOG lines appear in the console.
10. The CSV files copied from the MicroSD card open in the SD card log viewer.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Access is denied" or "port busy" when connecting | Close Arduino Serial Monitor, Serial Plotter, `predict_live.py`, `collect_data.py` and any other dashboard window, then connect again. |
| No COM port in the list | Check the USB cable (some are charge-only) and the driver for the board's USB port. Type the port name manually if needed. |
| "scikit-learn version differs" event | Run `setup_windows.bat` again so scikit-learn 1.8.0 is installed. |
| Browser shows "Dashboard server not reachable" | The console window was closed or crashed. Start `start_dashboard.bat` again and reload. |
| Port 8000 already in use | `start_dashboard.bat --http-port 8010`, then open http://127.0.0.1:8010 |
| Faculty want to view it from another device | `start_dashboard.bat --host 0.0.0.0`, then open http://LAPTOP-IP:8000 on the same Wi-Fi. Windows Firewall may ask for permission. |

## Notes from reading the firmware

* The comment near `ALERT_BUTTON_PIN` says GPIO4, but the code uses GPIO14. The dashboard shows GPIO14.
* `logFallEvent("FALL_CONFIRMED_SMS_SENT")` is written even when GSM was not ready, so this status alone does not prove that an SMS was sent. `gsm_log.csv` holds the modem reply.
* The two recipient phone numbers are in the firmware source. The dashboard masks them to the last 4 digits in the console, events and SD log viewer.
* The Random Forest runs on the laptop, not on the ESP32. The ESP32 streams data over USB and acts on the PRED commands it receives.
