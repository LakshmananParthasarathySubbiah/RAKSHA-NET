"""Build a single-file, hosted DEMO copy of the dashboard.

The hosted copy has no Python backend and no serial port, so it can only
replay recorded data. This script runs the real prediction engine (same code
as predict_live.py) over fall_dataset.csv in the same order as the dashboard's
demo replay, stores every result, and bundles the result with the normal
dashboard UI and a small in-browser stand-in for the demo backend
(static_demo/shim.js).

    python static_demo/build_static_demo.py  ->  static_demo/raksha_dashboard_demo.html
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASH = HERE.parent
sys.path.insert(0, str(DASH))

import pandas as pd  # noqa: E402

from backend.engine import EngineSettings, PredictionEngine  # noqa: E402
from backend.legacy import PREDICT_LIVE_DEFAULTS, load_model  # noqa: E402
from backend.ml_report import compute_report  # noqa: E402
from backend.paths import DATASET_FILE, MODEL_FILE, STATIC_DIR  # noqa: E402
from backend.sources import dataset_playlist  # noqa: E402

STATES = {"normal": 0, "check": 1, "suspected": 2, "fall": 3}


def precompute():
    model, info = load_model(MODEL_FILE)
    settings = EngineSettings()
    eng = PredictionEngine(model, settings)
    df = pd.read_csv(DATASET_FILE)
    rows = []
    for i, idx in enumerate(dataset_playlist(df)):
        r = df.loc[idx]
        line = f"MLDATA,{r.ax:.4f},{r.ay:.4f},{r.az:.4f},{r.gx:.4f},{r.gy:.4f},{r.gz:.4f}"
        res = eng.process(line, 1000.0 + i)  # one row per second, as in the dashboard demo
        if res.kind != "sample":
            raise RuntimeError(f"row {idx} was not a valid sample")
        if res.prediction == "straight":
            state = "normal"
        elif res.fall_count >= settings.fall_confirm_samples:
            state = "fall"
        elif res.fall_count >= 1:
            state = "suspected"
        else:
            state = "check"
        rows.append([int(idx), str(r.label)[0], round(float(r.ax), 4), round(float(r.ay), 4), round(float(r.az), 4),
                     round(float(r.gx), 4), round(float(r.gy), 4), round(float(r.gz), 4),
                     1 if res.prediction == "fall" else 0, round(res.probability, 4), round(res.p_fall, 4),
                     res.fall_count, STATES[state], res.command.strip(), 1 if res.write is not None else 0])
    model_info = {**info, "file": MODEL_FILE.name, "classes": [str(c) for c in model.classes_]}
    return rows, model_info


def main():
    rows, model_info = precompute()
    report = compute_report()
    data = {"rows": rows, "ml": report, "defaults": PREDICT_LIVE_DEFAULTS, "model": model_info,
            "settings": EngineSettings().as_dict()}

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    shim = (HERE / "shim.js").read_text(encoding="utf-8")

    # Fonts come from Google Fonts in the hosted copy
    css = re.sub(r"@font-face\s*\{[^}]*\}\s*", "", css)
    css = css.replace('--sans: "Plex Sans"', '--sans: "IBM Plex Sans"').replace('--mono: "Plex Mono"', '--mono: "IBM Plex Mono"')
    css += "\n.topbar { top: env(safe-area-inset-top, 0px); }\n.hosted-note { color: var(--ink-2); font-size: 13px; }\n"
    app = app.replace('font: { family: "Plex Mono" }', 'font: { family: "IBM Plex Mono" }')

    body = html.split("<body>", 1)[1].split("<script src=", 1)[0]
    # Downloads are blocked in the hosted viewer, so drop the CSV export link there
    body = body.replace('<a class="btn" href="/api/events.csv" download>Export CSV</a>', "")
    data_json = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    out = f"""<title>RAKSHA-NET Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
<style>
{css}
</style>
<script>
  try {{ const t = localStorage.getItem("raksha-theme"); if (t) document.documentElement.dataset.theme = t; }} catch (e) {{}}
</script>
{body}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.js"></script>
<script>window.__RAKSHA_DATA = {data_json};</script>
<script>
{shim}
</script>
<script>
{app}
</script>
"""
    target = HERE / "raksha_dashboard_demo.html"
    target.write_text(out, encoding="utf-8")
    falls = sum(1 for r in rows if r[14] and r[13].startswith("PRED,FALL"))
    print(f"wrote {target} ({len(out) / 1024:.0f} KB), {len(rows)} rows, {falls} PRED,FALL commands")


if __name__ == "__main__":
    main()
