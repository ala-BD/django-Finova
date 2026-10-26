"""M3 · Entraîne et évalue le modèle de risque de retard.

Régression logistique (StandardScaler + LogisticRegression pondérée), séparation 80 % / 20 %
stratifiée, seuil 0,5. Comparaison avec le modèle de référence « jamais de retard ».
Produit ml/m3_risk_model.joblib (chargé par settlements.risk) et ml/m3_risk_metrics.json
(chiffres de la diapositive de soutenance).

    python ml/m3_generate_dataset.py
    python ml/m3_train_risk.py
"""

import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from settlements.risk import FEATURES, THRESHOLD  # noqa: E402

DATASET = ROOT / "ml" / "m3_risk.csv"
MODEL_OUT = ROOT / "ml" / "m3_risk_model.joblib"
METRICS_OUT = ROOT / "ml" / "m3_risk_metrics.json"
VERSION = f"logreg-{date.today():%Y%m%d}"


def scores(y_true, y_pred):
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "accuracy": round(accuracy_score(y_true, y_pred), 3),
        "precision": round(precision_score(y_true, y_pred, zero_division=0), 3),
        "recall": round(recall_score(y_true, y_pred, zero_division=0), 3),
        "f1": round(f1_score(y_true, y_pred, zero_division=0), 3),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def main():
    with open(DATASET, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    X = [[float(row[name]) for name in FEATURES] for row in rows]
    y = [int(row["late"]) for row in rows]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )

    # Classes déséquilibrées (~35 % de retards) : on repondère pour ne pas sacrifier le rappel
    pipeline = make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced")
    )
    pipeline.fit(X_train, y_train)

    probabilities = pipeline.predict_proba(X_test)[:, 1]
    model_pred = [int(p >= THRESHOLD) for p in probabilities]
    baseline_pred = [0] * len(y_test)  # référence « jamais de retard »

    started = time.perf_counter()
    for row in X_test:
        pipeline.predict_proba([row])
    latency_ms = (time.perf_counter() - started) * 1000 / len(X_test)

    coefficients = pipeline[-1].coef_[0]
    metrics = {
        "version": VERSION,
        "dataset": {"rows": len(rows), "train": len(X_train), "test": len(X_test),
                    "late_rate": round(sum(y) / len(y), 3), "generated": True},
        "threshold": THRESHOLD,
        "model": scores(y_test, model_pred),
        "baseline_never_late": scores(y_test, baseline_pred),
        "prediction_latency_ms": round(latency_ms, 3),
        "standardized_coefficients": {
            name: round(float(c), 3) for name, c in zip(FEATURES, coefficients)
        },
    }

    joblib.dump({"pipeline": pipeline, "features": FEATURES, "version": VERSION}, MODEL_OUT)
    METRICS_OUT.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    m, b = metrics["model"], metrics["baseline_never_late"]
    print(f"Jeu : {len(rows)} lignes générées, {metrics['dataset']['late_rate']:.0%} de retards")
    print(f"{'':22}{'modèle':>10}{'référence':>12}")
    for key in ("accuracy", "precision", "recall", "f1"):
        print(f"{key:22}{m[key]:>10}{b[key]:>12}")
    print(f"Matrice de confusion (modèle) : {m['confusion_matrix']}")
    print(f"Latence moyenne d'une prédiction : {latency_ms:.2f} ms")
    print(f"Modèle → {MODEL_OUT.name}, métriques → {METRICS_OUT.name}")


if __name__ == "__main__":
    main()
