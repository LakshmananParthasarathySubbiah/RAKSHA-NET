import argparse
import csv
import re
import time
from pathlib import Path

import serial


DEFAULT_DIRECTIONS = ["front", "right", "back", "left"]
SENSOR_TEXT_RE = re.compile(
    r"ACC X:\s*([-+]?\d+(?:\.\d+)?)\s*g,\s*"
    r"Y:\s*([-+]?\d+(?:\.\d+)?)\s*g,\s*"
    r"Z:\s*([-+]?\d+(?:\.\d+)?)\s*g\s*\|\s*"
    r"GYRO X:\s*([-+]?\d+(?:\.\d+)?)\s*dps,\s*"
    r"Y:\s*([-+]?\d+(?:\.\d+)?)\s*dps,\s*"
    r"Z:\s*([-+]?\d+(?:\.\d+)?)\s*dps"
)

HEADER = [
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
    "acc_mag",
    "gyro_mag",
    "label",
]


def parse_ml_line(line):
    if line.startswith("MLDATA,"):
        parts = line.split(",")
        if len(parts) != 7:
            return None

        ax = float(parts[1])
        ay = float(parts[2])
        az = float(parts[3])
        gx = float(parts[4])
        gy = float(parts[5])
        gz = float(parts[6])
    else:
        match = SENSOR_TEXT_RE.search(line)
        if match is None:
            return None

        ax, ay, az, gx, gy, gz = [float(value) for value in match.groups()]

    acc_mag = (ax * ax + ay * ay + az * az) ** 0.5
    gyro_mag = (gx * gx + gy * gy + gz * gz) ** 0.5

    return [ax, ay, az, gx, gy, gz, acc_mag, gyro_mag]


def alert(message):
    print(f"\n\n*** {message} ***")
    try:
        import winsound

        winsound.Beep(1200, 700)
    except Exception:
        print("\a", end="", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Collect ESP32 fall-detection training data.")
    parser.add_argument("--port", required=True, help="Serial port, for example COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--label", required=True, choices=["straight", "fall"])
    parser.add_argument("--seconds", type=int, default=240)
    parser.add_argument("--direction-seconds", type=int, default=60)
    parser.add_argument(
        "--directions",
        nargs="+",
        default=DEFAULT_DIRECTIONS,
        help="Direction prompts to cycle through during collection.",
    )
    parser.add_argument("--output", default="fall_dataset.csv")
    args = parser.parse_args()

    output_path = Path(args.output)
    write_header = not output_path.exists()

    print(f"Collecting label={args.label} for {args.seconds} seconds from {args.port}")
    print(
        "Direction plan: "
        + ", ".join(f"{direction}={args.direction_seconds}s" for direction in args.directions)
    )
    print("Start the movement when the first alert appears...")

    rows_written = 0
    start_time = time.time()
    end_time = start_time + args.seconds
    current_direction_index = -1

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        with output_path.open("a", newline="") as file:
            writer = csv.writer(file)
            if write_header:
                writer.writerow(HEADER)

            while time.time() < end_time:
                elapsed = time.time() - start_time
                direction_index = int(elapsed // args.direction_seconds) % len(args.directions)
                if direction_index != current_direction_index:
                    current_direction_index = direction_index
                    alert(
                        f"{args.label.upper()} data: face/move {args.directions[direction_index].upper()} "
                        f"for next {args.direction_seconds} seconds"
                    )

                raw_line = ser.readline().decode(errors="ignore").strip()
                row = parse_ml_line(raw_line)
                if row is None:
                    continue

                row.append(args.label)
                writer.writerow(row)
                rows_written += 1
                print(f"\rRows: {rows_written}", end="", flush=True)

    print(f"\nSaved {rows_written} rows to {output_path}")


if __name__ == "__main__":
    main()
