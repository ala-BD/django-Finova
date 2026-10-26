"""
Script d'entraînement du classifieur de catégories de dépenses (Module M2 - Yahya).
Entraîne un pipeline TF-IDF + Logistic Regression sur ml/m2_categories.csv.
Évalue la performance (F1-score macro, matrice de confusion, temps de latence).
Sauvegarde le modèle sérialisé dans ml/m2_categorizer.joblib.
"""

import csv
import json
import time
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline


def train_model():
    base_dir = Path(__file__).resolve().parent
    data_file = base_dir / "m2_categories.csv"
    model_file = base_dir / "m2_categorizer.joblib"
    metrics_file = base_dir / "m2_metrics.json"

    print(f"Chargement des données depuis {data_file}...")
    titles = []
    categories = []
    with open(data_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            titles.append(row["title"].strip())
            categories.append(row["category"].strip())

    print(f"Nombre total d'échantillons étiquetés : {len(titles)}")
    X = titles
    y = categories

    # Division 80% entraînement / 20% test avec stratification
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    # Pipeline TF-IDF (unigrammes et bigrammes) + Régression Logistique multinationale
    pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    strip_accents="unicode",
                    lowercase=True,
                    min_df=1,
                    sublinear_tf=True,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    C=10.0,
                    max_iter=500,
                    random_state=42,
                    solver="lbfgs",
                ),
            ),
        ]
    )

    print("\nEntraînement du pipeline scikit-learn...")
    t0 = time.perf_counter()
    pipeline.fit(X_train, y_train)
    train_duration = time.perf_counter() - t0
    print(f"Entraînement terminé en {train_duration:.3f} s.")

    # Évaluation sur le jeu de test
    y_pred = pipeline.predict(X_test)
    f1_macro = float(f1_score(y_test, y_pred, average="macro"))
    print(f"\n==========================================")
    print(f"F1-score MACRO obtenu : {f1_macro:.4f} (Objectif spec: >= 0.80)")
    print(f"==========================================\n")

    classes = sorted(list(set(y)))
    cm = confusion_matrix(y_test, y_pred, labels=classes)
    report_dict = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
    report_str = classification_report(y_test, y_pred, zero_division=0)
    print(report_str)

    # Mesure de latence d'inférence unitaire
    test_samples = ["Courses Monoprix", "Billet train SNCFT", "Loyer mensuel", "Places de cinema"]
    latencies = []
    for s in test_samples:
        t_start = time.perf_counter()
        _ = pipeline.predict([s])[0]
        latencies.append((time.perf_counter() - t_start) * 1000)
    avg_latency_ms = sum(latencies) / len(latencies)
    print(f"Latence moyenne d'inférence : {avg_latency_ms:.2f} ms (< 50 ms)")

    # Sauvegarde du modèle
    joblib.dump(pipeline, model_file)
    print(f"\nModèle sérialisé sauvegardé dans : {model_file}")

    # Sauvegarde des métriques pour la soutenance orale
    metrics_data = {
        "dataset_size": len(titles),
        "classes": classes,
        "f1_score_macro": round(f1_macro, 4),
        "target_f1_macro": 0.80,
        "f1_target_met": f1_macro >= 0.80,
        "avg_latency_ms": round(avg_latency_ms, 2),
        "confusion_matrix": cm.tolist(),
        "classification_report": report_dict,
    }
    with open(metrics_file, "w", encoding="utf-8") as f:
        json.dump(metrics_data, f, indent=2, ensure_ascii=False)
    print(f"Métriques sauvegardées dans : {metrics_file}")

    return f1_macro


if __name__ == "__main__":
    train_model()
