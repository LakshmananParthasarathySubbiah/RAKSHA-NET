# RAKSHA-NET dashboard, online demo

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
