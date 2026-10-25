"""Machine-learning results computed from the project's own files.

Nothing here is typed in by hand. The report:
  1. counts the rows per label in each dataset CSV,
  2. repeats the train/test split from train_random_forest.py
     (test_size=0.25, random_state=42, stratify=label),
  3. evaluates the saved model (fall_random_forest.joblib) on that test split,
  4. retrains a RandomForest with the script's settings on the training split
     and checks whether it gives the same predictions as the saved model
     (this shows whether the saved model came from this dataset and split),
  5. reads the saved model's feature importances.

Run directly to print the report:  python -m backend.ml_report
"""
import hashlib
import json
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import train_test_split

from .legacy import FEATURES, load_model
from .paths import CACHE_DIR, DATASET_FILE, EXTRA_DATASET_FILES, MODEL_FILE

REPORT_VERSION = 3

# Settings from train_random_forest.py
TRAIN_SETTINGS = {
    "features": FEATURES,
    "test_size": 0.25,
    "random_state": 42,
    "stratify": "label",
    "n_estimators": 200,
    "class_weight": "balanced",
}


def _file_key(paths):
    h = hashlib.sha256()
    for p in paths:
        if p.exists():
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    h.update(str(REPORT_VERSION).encode())
    return h.hexdigest()[:16]


def _metrics(y_true, y_pred, labels):
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    per_class = {
        lab: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]), "support": int(s[i])}
        for i, lab in enumerate(labels)
    }
    mp, mr, mf, _ = precision_recall_fscore_support(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    return {
        "labels": labels,
        "confusion_matrix": cm.tolist(),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "per_class": per_class,
        "macro": {"precision": float(mp), "recall": float(mr), "f1": float(mf)},
        "n_test": int(len(y_true)),
    }


def compute_report():
    t0 = time.time()
    report = {"generated_at": t0, "notes": []}

    # 1. Datasets
    files = []
    main = pd.read_csv(DATASET_FILE)
    for path in [DATASET_FILE] + list(EXTRA_DATASET_FILES):
        if not path.exists():
            continue
        df = main if path == DATASET_FILE else pd.read_csv(path)
        entry = {
            "file": path.name,
            "rows": int(len(df)),
            "labels": {str(k): int(v) for k, v in df["label"].value_counts().items()},
            "used_for_training": path == DATASET_FILE,
        }
        if path != DATASET_FILE:
            head = main.iloc[: len(df)].reset_index(drop=True)
            same_cols = list(df.columns) == list(main.columns)
            entry["subset_of_main"] = bool(same_cols and head.equals(df.reset_index(drop=True)))
        files.append(entry)
    report["datasets"] = files

    data = main.dropna(subset=FEATURES + ["label"])
    report["dropped_na_rows"] = int(len(main) - len(data))
    report["feature_stats"] = {
        lab: {f: {"mean": float(g[f].mean()), "std": float(g[f].std())} for f in FEATURES}
        for lab, g in data.groupby("label")
    }

    x = data[FEATURES]
    y = data["label"]
    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=TRAIN_SETTINGS["test_size"], random_state=TRAIN_SETTINGS["random_state"], stratify=y
    )
    report["split"] = {"train_rows": int(len(x_train)), "test_rows": int(len(x_test)),
                       "train_labels": {str(k): int(v) for k, v in y_train.value_counts().items()},
                       "test_labels": {str(k): int(v) for k, v in y_test.value_counts().items()}}

    # 2. Saved model
    model, versions = load_model(MODEL_FILE)
    labels = [str(c) for c in model.classes_]
    report["model"] = {
        "file": MODEL_FILE.name,
        "type": type(model).__name__,
        "classes": labels,
        "n_features_in": int(getattr(model, "n_features_in_", len(FEATURES))),
        "feature_names": [str(f) for f in getattr(model, "feature_names_in_", FEATURES)],
        "params": {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v))
                   for k, v in model.get_params().items()},
        "n_trees": len(getattr(model, "estimators_", [])),
        "max_tree_depth": int(max(e.tree_.max_depth for e in model.estimators_)),
        "mean_tree_leaves": float(np.mean([e.tree_.n_leaves for e in model.estimators_])),
        **versions,
    }
    report["train_settings"] = TRAIN_SETTINGS

    saved_pred = model.predict(x_test)
    report["saved_model_test"] = _metrics(y_test.to_numpy(), saved_pred, labels)

    # Also on the full dataset (mostly training rows; shown only as a consistency check)
    full_pred = model.predict(x)
    report["saved_model_all_rows_accuracy"] = float(accuracy_score(y, full_pred))

    # 3. Reproduce train_random_forest.py and compare
    repro = RandomForestClassifier(n_estimators=TRAIN_SETTINGS["n_estimators"],
                                   random_state=TRAIN_SETTINGS["random_state"],
                                   class_weight=TRAIN_SETTINGS["class_weight"])
    repro.fit(x_train, y_train)
    repro_pred = repro.predict(x_test)
    agree_test = float(np.mean(repro_pred == saved_pred))
    agree_all = float(np.mean(repro.predict(x) == full_pred))
    proba_diff = float(np.max(np.abs(repro.predict_proba(x) - model.predict_proba(x))))
    report["reproduction"] = {
        "metrics": _metrics(y_test.to_numpy(), repro_pred, labels),
        "agreement_test": agree_test,
        "agreement_all_rows": agree_all,
        "max_probability_difference": proba_diff,
        "identical": bool(agree_all == 1.0 and proba_diff < 1e-9),
    }

    # 4. Feature importance (mean decrease in impurity, from the saved model)
    imp = getattr(model, "feature_importances_", None)
    if imp is not None:
        names = report["model"]["feature_names"]
        report["feature_importance"] = sorted(
            [{"feature": n, "importance": float(v)} for n, v in zip(names, imp)],
            key=lambda d: d["importance"], reverse=True)

    report["compute_seconds"] = round(time.time() - t0, 2)
    return report


def get_report(force=False):
    key = _file_key([DATASET_FILE, MODEL_FILE, *EXTRA_DATASET_FILES])
    cache = CACHE_DIR / f"ml_report_{key}.json"
    if cache.exists() and not force:
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass
    report = compute_report()
    report["cache_key"] = key
    cache.write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    r = get_report(force=True)
    m = r["saved_model_test"]
    print("Datasets:", json.dumps(r["datasets"], indent=1))
    print("Model:", r["model"]["type"], r["model"]["classes"], "trained with sklearn", r["model"]["trained_sklearn"])
    print("Test rows:", m["n_test"], "labels", m["labels"])
    print("Confusion matrix:", m["confusion_matrix"])
    print("Accuracy: %.4f" % m["accuracy"])
    for lab, v in m["per_class"].items():
        print(f"  {lab:9s} P={v['precision']:.4f} R={v['recall']:.4f} F1={v['f1']:.4f} n={v['support']}")
    print("Reproduction identical to saved model:", r["reproduction"]["identical"],
          "agreement_all_rows=%.4f" % r["reproduction"]["agreement_all_rows"])
    print("Feature importance:", [(d["feature"], round(d["importance"], 4)) for d in r["feature_importance"]])
