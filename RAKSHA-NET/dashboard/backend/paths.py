"""File locations used by the dashboard.

The dashboard lives in RAKSHA-NET/dashboard and reuses the original files in
RAKSHA-NET/ml_laptop (scripts, model and dataset) without copying them.
"""
import os
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = DASHBOARD_DIR.parent
ML_DIR = PROJECT_DIR / "ml_laptop"
FIRMWARE_FILE = PROJECT_DIR / "falldeetectml" / "falldeetectml.ino"

MODEL_FILE = ML_DIR / "fall_random_forest.joblib"
DATASET_FILE = ML_DIR / "fall_dataset.csv"
EXTRA_DATASET_FILES = [ML_DIR / "straight_fall_dataset - Copy.csv"]
TRAIN_SCRIPT = ML_DIR / "train_random_forest.py"
PREDICT_SCRIPT = ML_DIR / "predict_live.py"

STATIC_DIR = DASHBOARD_DIR / "static"
# RAKSHA_DATA_DIR moves recordings/logs/cache elsewhere (the tests use it so they
# never write into the real folders).
_DATA = Path(os.environ["RAKSHA_DATA_DIR"]) if os.environ.get("RAKSHA_DATA_DIR") else DASHBOARD_DIR
RECORDINGS_DIR = _DATA / "recordings"
SD_LOGS_DIR = _DATA / "sd_logs"
LOGS_DIR = _DATA / "logs"
CACHE_DIR = _DATA / "cache"

for _d in (RECORDINGS_DIR, SD_LOGS_DIR, LOGS_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
