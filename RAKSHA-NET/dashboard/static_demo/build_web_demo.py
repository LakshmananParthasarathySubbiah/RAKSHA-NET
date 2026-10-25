"""Make dashboard/web_demo/: a static website (for Vercel, Netlify or GitHub Pages)
with the same demo replay as the hosted page. Run build_static_demo.py first.

    python static_demo/build_web_demo.py
"""
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASH = HERE.parent
OUT = DASH / "web_demo"

page = (HERE / "raksha_dashboard_demo.html").read_text(encoding="utf-8")
page = page.replace('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.js"></script>',
                    '<script src="vendor/chart.umd.js"></script>')
split = page.index('<header class="topbar">')
head, body = page[:split], page[split:]
html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="description" content="RAKSHA-NET fall detection dashboard, online demo replay of recorded data">\n'
        + head + '</head>\n<body>\n' + body + '</body>\n</html>\n')
OUT.mkdir(exist_ok=True)
(OUT / "vendor").mkdir(exist_ok=True)
(OUT / "index.html").write_text(html, encoding="utf-8")
shutil.copy(DASH / "static" / "vendor" / "chart.umd.js", OUT / "vendor" / "chart.umd.js")
shutil.copy(DASH / "static" / "vendor" / "chart.js.LICENSE.md", OUT / "vendor" / "chart.js.LICENSE.md")
(OUT / "README.md").write_text("""# RAKSHA-NET dashboard, online demo

Static website version of the dashboard. It replays the recorded rows of
`ml_laptop/fall_dataset.csv` with predictions computed in advance by the saved
Random Forest, using the same logic as `predict_live.py`. It has no access to
the ESP32; live monitoring needs `dashboard/start_dashboard.bat` on the laptop.

## Deploy on Vercel

1. vercel.com, sign in with GitHub, Add New, Project.
2. Import the RAKSHA-NET repository.
3. Root Directory: `dashboard/web_demo`. Framework Preset: Other. No build command.
4. Deploy. The site is served from `index.html`.

To rebuild after changing the dashboard or model:
`python dashboard/static_demo/build_static_demo.py` then `python dashboard/static_demo/build_web_demo.py`.
""", encoding="utf-8")
print("wrote", OUT, round(len(html) / 1024), "KB")
