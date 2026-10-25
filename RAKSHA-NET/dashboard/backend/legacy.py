"""Bridge to the original laptop scripts in ml_laptop/.

The dashboard does not re-implement sample parsing or the board command
format. It imports them from the original files so the behaviour stays the
same as collect_data.py and predict_live.py.
"""
import sys
import warnings

from .paths import ML_DIR

if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from collect_data import parse_ml_line  # noqa: E402
from predict_live import FEATURES, board_command, has_real_sensor_data  # noqa: E402

# Defaults copied from the argparse block in predict_live.py main().
# tests/test_engine_equivalence.py checks these against the script source.
PREDICT_LIVE_DEFAULTS = {
    "baud": 115200,
    "alert_threshold": 0.70,
    "send_interval": 1.0,
    "fall_confirm_samples": 3,
    "min_acc_mag": 0.20,
    "stale_seconds": 3.0,
    "no_board_alert": False,
}


def load_model(path):
    """Load the joblib model and report any scikit-learn version mismatch."""
    import joblib

    trained_with = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = joblib.load(path)
    for w in caught:
        if type(w.message).__name__ == "InconsistentVersionWarning":
            trained_with = getattr(w.message, "original_sklearn_version", None)
            break
    import sklearn

    return model, {
        "running_sklearn": sklearn.__version__,
        "trained_sklearn": trained_with or sklearn.__version__,
        "version_mismatch": trained_with is not None,
    }


__all__ = [
    "FEATURES",
    "PREDICT_LIVE_DEFAULTS",
    "board_command",
    "has_real_sensor_data",
    "load_model",
    "parse_ml_line",
]
