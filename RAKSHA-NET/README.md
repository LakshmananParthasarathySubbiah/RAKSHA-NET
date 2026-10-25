# RAKSHA-NET

Fall detection prototype built on an ESP32-S3 with an MPU6500 IMU, a Random Forest classifier running on a laptop, BLE room identification and SMS alerts through a SIM800L.

Embedded Systems, DA3 Review 3: Prototype Validation, Vellore Institute of Technology, Chennai.
Team: Disha Parasu, Lakshmanan Parthasarathy SP, Nethra Sri R S (23BAI1288, 24BCE1712, 24BCE1857). Faculty guide: Dr Suhasini S.

## How it works

1. The ESP32-S3 reads the MPU6500 (±2 g, ±250 dps) once per loop and prints an `MLDATA,ax,ay,az,gx,gy,gz` line over USB serial (115200 baud).
2. `predict_live.py` on the laptop adds `acc_mag` and `gyro_mag`, runs the Random Forest (200 trees) and sends `PRED,FALL|NORMAL|CHECK,<confidence>` back to the board.
3. `PRED,FALL` is sent only after 3 consecutive fall predictions with confidence of at least 0.70.
4. The board then sounds the buzzer and shows a 12 s countdown on the LCD. Pressing the button cancels the alert. Otherwise the SIM800L sends an SMS with the room, RSSI and confidence to two numbers.
5. A FreeRTOS task on core 0 scans for BLE beacons named `ROOM_Bedroom`, `ROOM_Hallroom` and `ROOM_Washroom`. The strongest one above −95 dBm is taken as the current room.
6. Alert events and modem replies are written to CSV files on the MicroSD card.

## Repository layout

| Folder | Contents |
| --- | --- |
| `falldeetectml/` | ESP32-S3 firmware (Arduino) |
| `ml_laptop/` | Data collection, training and live prediction scripts, dataset (`fall_dataset.csv`, 1425 rows) and trained model |
| `dashboard/` | Web dashboard for the laptop: live serial monitoring, alert stages, room, ML results, event history. See `dashboard/README.md` |
| `firmware_event_patch/` | Optional copy of the firmware that also prints alert events on serial (compiled, not yet flashed) |
| `dashboard/web_demo/` | Static website version of the dashboard (demo replay), deployed on Vercel |
| `docs/` | DA3 Review 3 presentation and the references and proofs document |

## Before flashing

The two recipient numbers in `falldeetectml/falldeetectml.ino` are replaced with `PHONE_NUMBER_1_HERE` and `PHONE_NUMBER_2_HERE` in this public repository. Put the real numbers in your local copy before uploading.

## Model results

On the held-out 25% test split (357 rows, stratified, `random_state=42`): accuracy 96.9%, fall precision 97.1%, fall recall 95.1%. These are per-sample results from a random row split, not results from hardware trials.

## How to run

### Online demo (no hardware, no install)

Dashboard demo: [raksha-net-da3-vitc.vercel.app](https://raksha-net-da3-vitc.vercel.app). It replays the recorded dataset with the saved model's predictions. Live monitoring of the ESP32 needs the laptop setup below.

### On a Windows laptop

1. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH").
2. Download this repository: green Code button, Download ZIP, then extract it.
3. Open `dashboard` and double-click `setup_windows.bat` once. It installs the Python packages.
4. Without the board: double-click `start_demo.bat`. The dashboard opens at http://127.0.0.1:8000.
5. With the board: flash `falldeetectml/falldeetectml.ino` (put your own phone numbers in first), plug the ESP32-S3 in by USB, close Arduino Serial Monitor, then double-click `start_dashboard.bat`, click Data source, pick the COM port and click Connect.

### Laptop scripts only (no dashboard)

```
cd ml_laptop
pip install pyserial pandas scikit-learn==1.8.0 joblib
python collect_data.py --port COM6 --label straight
python train_random_forest.py
python predict_live.py --port COM6 --model fall_random_forest.joblib
```

## Links

- Online dashboard demo: https://raksha-net-da3-vitc.vercel.app
- Demonstration video: https://drive.google.com/file/d/1NSicNeXJ_TEGcQyVMQZT8Lcgb-owuh-1/view
- IEEE project report: https://docs.google.com/document/d/1tkphE-E_hh2wXWzOzW7m_2iI8aC8j3y8QR7x9MrlkrM/edit?usp=sharing
- References and proofs: https://docs.google.com/document/d/1VNs3Jc9mYc-b4mMi58AH8aHW2Y_WUNgGyTsJXDkRRuY/edit?usp=sharing
