import argparse
import re
import time

import serial


SENSOR_TEXT_RE = re.compile(r"ACC X:.*GYRO X:")


def main():
    parser = argparse.ArgumentParser(description="Print raw ESP32 serial data for debugging.")
    parser.add_argument("--port", required=True, help="Serial port, for example COM6")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()

    print(f"Opening {args.port} at {args.baud} baud...")
    rows_seen = 0
    ml_rows_seen = 0
    sensor_text_rows_seen = 0
    end_time = time.time() + args.seconds

    try:
        with serial.Serial(args.port, args.baud, timeout=1) as ser:
            print(f"Port open: {ser.is_open}")
            print("Waiting for ESP32 data...")

            try:
                while time.time() < end_time:
                    raw_bytes = ser.readline()
                    if not raw_bytes:
                        print(".", end="", flush=True)
                        continue

                    line = raw_bytes.decode(errors="replace").strip()
                    rows_seen += 1
                    if line.startswith("MLDATA,"):
                        ml_rows_seen += 1
                    if SENSOR_TEXT_RE.search(line):
                        sensor_text_rows_seen += 1

                    print(f"\n[{rows_seen}] {line}")
            except KeyboardInterrupt:
                print("\nStopped by user.")

    except serial.SerialException as exc:
        print(f"Serial error: {exc}")
        return

    print()
    print(
        f"Finished. Lines seen: {rows_seen}, "
        f"MLDATA lines seen: {ml_rows_seen}, "
        f"sensor text lines seen: {sensor_text_rows_seen}"
    )


if __name__ == "__main__":
    main()
