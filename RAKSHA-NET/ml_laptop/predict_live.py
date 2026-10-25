import argparse
import time

import joblib
import pandas as pd
import serial

from collect_data import parse_ml_line


FEATURES = ["ax", "ay", "az", "gx", "gy", "gz", "acc_mag", "gyro_mag"]


def has_real_sensor_data(row, min_acc_mag):
    acc_mag = row[6]
    gyro_mag = row[7]
    return acc_mag >= min_acc_mag or gyro_mag > 0.5


def board_command(prediction, probability, alert_threshold):
    prediction = str(prediction).strip().lower()
    if prediction == "fall" and probability >= alert_threshold:
        return f"PRED,FALL,{probability:.2f}\n"
    if prediction == "straight":
        return f"PRED,NORMAL,{probability:.2f}\n"
    return f"PRED,CHECK,{probability:.2f}\n"


def main():
    parser = argparse.ArgumentParser(description="Run live RandomForest fall prediction from ESP32 serial.")
    parser.add_argument("--port", required=True, help="Serial port, for example COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--model", default="fall_random_forest.joblib")
    parser.add_argument("--alert-threshold", type=float, default=0.70)
    parser.add_argument("--send-interval", type=float, default=1.0)
    parser.add_argument("--fall-confirm-samples", type=int, default=3)
    parser.add_argument("--min-acc-mag", type=float, default=0.20)
    parser.add_argument("--stale-seconds", type=float, default=3.0)
    parser.add_argument("--no-board-alert", action="store_true")
    args = parser.parse_args()

    model = joblib.load(args.model)
    print("Live prediction running. Press Ctrl+C to stop.")
    print("Close Arduino Serial Monitor before running this script.")
    last_sent_at = 0.0
    last_data_at = 0.0
    consecutive_fall_count = 0

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        try:
            while True:
                now = time.time()
                raw_line = ser.readline().decode(errors="ignore").strip()
                row = parse_ml_line(raw_line) if raw_line.startswith("MLDATA,") else None
                if row is None:
                    if (
                        last_data_at
                        and now - last_data_at >= args.stale_seconds
                        and not args.no_board_alert
                        and now - last_sent_at >= args.send_interval
                    ):
                        ser.write(b"PRED,CHECK,0.00\n")
                        last_sent_at = now
                        consecutive_fall_count = 0
                        print("No fresh sensor data | board: PRED,CHECK,0.00")
                    continue

                if not has_real_sensor_data(row, args.min_acc_mag):
                    consecutive_fall_count = 0
                    print(f"Ignoring invalid sensor sample: {row}")
                    continue

                last_data_at = now

                sample = pd.DataFrame([row], columns=FEATURES)
                prediction = model.predict(sample)[0]
                probability = max(model.predict_proba(sample)[0])
                normalized_prediction = str(prediction).strip().lower()

                if normalized_prediction == "fall" and probability >= args.alert_threshold:
                    consecutive_fall_count += 1
                else:
                    consecutive_fall_count = 0

                if consecutive_fall_count >= args.fall_confirm_samples:
                    command = board_command(prediction, probability, args.alert_threshold)
                elif normalized_prediction == "straight":
                    command = board_command(prediction, probability, args.alert_threshold)
                else:
                    command = f"PRED,CHECK,{probability:.2f}\n"

                if not args.no_board_alert and (command.startswith("PRED,FALL") or now - last_sent_at >= args.send_interval):
                    ser.write(command.encode())
                    last_sent_at = now

                print(
                    f"Prediction: {prediction} | confidence: {probability:.2f} "
                    f"| fall_count: {consecutive_fall_count}/{args.fall_confirm_samples} "
                    f"| board: {command.strip()}"
                )
        except KeyboardInterrupt:
            if not args.no_board_alert:
                ser.write(b"PRED,NORMAL,0.00\n")
            print("\nStopped live prediction.")


if __name__ == "__main__":
    main()