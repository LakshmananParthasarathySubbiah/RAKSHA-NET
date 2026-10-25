import argparse

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split


FEATURES = ["ax", "ay", "az", "gx", "gy", "gz", "acc_mag", "gyro_mag"]


def main():
    parser = argparse.ArgumentParser(description="Train RandomForest fall detector.")
    parser.add_argument("--data", default="fall_dataset.csv")
    parser.add_argument("--model", default="fall_random_forest.joblib")
    args = parser.parse_args()

    data = pd.read_csv(args.data)
    data = data.dropna(subset=FEATURES + ["label"])

    x = data[FEATURES]
    y = data["label"]

    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=0.25,
        random_state=42,
        stratify=y,
    )

    model = RandomForestClassifier(
        n_estimators=200,
        random_state=42,
        class_weight="balanced",
    )

    model.fit(x_train, y_train)
    predictions = model.predict(x_test)

    print("Confusion matrix:")
    print(confusion_matrix(y_test, predictions))
    print()
    print(classification_report(y_test, predictions))

    joblib.dump(model, args.model)
    print(f"Saved model to {args.model}")


if __name__ == "__main__":
    main()
