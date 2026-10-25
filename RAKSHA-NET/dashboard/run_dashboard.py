"""Start the RAKSHA-NET dashboard.

    python run_dashboard.py                    open the dashboard, choose live or demo in the browser
    python run_dashboard.py --serial-port COM6 connect to the ESP32 on COM6 at start
    python run_dashboard.py --demo             start the clearly labelled demo replay

The prediction options have the same names and defaults as predict_live.py.
Only this program should have the serial port open while it runs.
"""
import argparse
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.engine import EngineSettings  # noqa: E402
from backend.legacy import PREDICT_LIVE_DEFAULTS  # noqa: E402


def main():
    d = PREDICT_LIVE_DEFAULTS
    p = argparse.ArgumentParser(description="RAKSHA-NET web dashboard")
    p.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to allow other devices on the network")
    p.add_argument("--http-port", type=int, default=8000)
    p.add_argument("--serial-port", "--port", dest="serial_port", default=None, help="for example COM6")
    p.add_argument("--baud", type=int, default=d["baud"])
    p.add_argument("--demo", action="store_true", help="start the demo replay of fall_dataset.csv")
    p.add_argument("--no-browser", action="store_true")
    # Same options as predict_live.py
    p.add_argument("--alert-threshold", type=float, default=d["alert_threshold"])
    p.add_argument("--send-interval", type=float, default=d["send_interval"])
    p.add_argument("--fall-confirm-samples", type=int, default=d["fall_confirm_samples"])
    p.add_argument("--min-acc-mag", type=float, default=d["min_acc_mag"])
    p.add_argument("--stale-seconds", type=float, default=d["stale_seconds"])
    p.add_argument("--no-board-alert", action="store_true")
    args = p.parse_args()

    settings = EngineSettings(alert_threshold=args.alert_threshold, send_interval=args.send_interval,
                              fall_confirm_samples=args.fall_confirm_samples, min_acc_mag=args.min_acc_mag,
                              stale_seconds=args.stale_seconds, no_board_alert=args.no_board_alert)

    from backend.app import create_app
    from backend.sources import DatasetReplayWorker, SerialWorker

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.http_port}/"

    def startup(app):
        # Runs once the web server has started.
        hub = app.state.hub
        time.sleep(0.5)
        print(f"  Ready: {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        if args.serial_port:
            hub.start_worker("live", {"kind": "serial", "port": args.serial_port, "baud": args.baud,
                                      "label": f"{args.serial_port} @ {args.baud}", "settings": settings.as_dict()},
                             lambda h: SerialWorker(h, args.serial_port, args.baud))
        elif args.demo:
            hub.start_worker("demo", {"kind": "dataset", "label": "fall_dataset.csv (recorded training samples)",
                                      "settings": settings.as_dict()}, lambda h: DatasetReplayWorker(h))

    app = create_app(settings, startup=startup)
    print("RAKSHA-NET dashboard", flush=True)
    print(f"  Starting on {url} ... (press Ctrl+C in this window to stop)", flush=True)
    print("  Close Arduino Serial Monitor and predict_live.py before connecting to the board.", flush=True)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.http_port, log_level="warning")


if __name__ == "__main__":
    main()
