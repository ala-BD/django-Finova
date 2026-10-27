"""
Script d'entraînement du classifieur de catégories de dépenses (Module M2 - Yahya).
Entraîne un pipeline TF-IDF + Logistic Regression sur ml/m2_categories.csv
(+ ml/m2_categories_extra.csv : titres courts tels qu'on les tape vraiment).
Évalue la performance (F1-score macro, matrice de confusion, temps de latence).
Sauvegarde le modèle sérialisé dans ml/m2_categorizer.joblib.

Évaluation honnête :
- le jeu d'origine contient chaque titre deux fois (« courses lidl » et « note courses lidl ») :
  un découpage aléatoire mettait l'un en entraînement et l'autre en test, ce qui gonflait le F1.
  Le découpage 80/20 est donc groupé par phrase de base (StratifiedGroupKFold) ;
- ml/m2_real_titles.csv (40 titres réalistes) n'est JAMAIS utilisé pour l'entraînement :
  c'est le contrôle final, le plus proche de l'usage réel ;
- les données sont synthétiques (rédigées à la main), ce qui est dit sur la slide.
"""

import csv
import json
import time
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedGroupKFold, train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

BASE_DIR = Path(__file__).resolve().parent
# Préfixes ajoutés au jeu d'origine pour doubler les lignes (« paiement courses monoprix »)
AUGMENT_PREFIXES = ("paiement ", "note ", "frais ", "achat ", "reglement ", "solde ")


def load_csv(name):
    with open(BASE_DIR / name, "r", encoding="utf-8") as f:
        rows = [(row["title"].strip(), row["category"].strip()) for row in csv.DictReader(f)]
    return [row for row in rows if row[0] and row[1]]


def base_phrase(title, known):
    """« note courses lidl » → « courses lidl » si cette phrase existe aussi dans le jeu."""
    for prefix in AUGMENT_PREFIXES:
        if title.startswith(prefix) and title[len(prefix):] in known:
            return title[len(prefix):]
    return title


def build_pipeline():
    # Mots (1-2) + n-grammes de caractères : robuste aux titres très courts et aux fautes
    features = FeatureUnion([
        ("words", TfidfVectorizer(ngram_range=(1, 2), strip_accents="unicode", lowercase=True, sublinear_tf=True)),
        ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), strip_accents="unicode",
                                  lowercase=True, sublinear_tf=True)),
    ])
    return Pipeline([
        ("tfidf", features),
        ("clf", LogisticRegression(C=10.0, max_iter=2000, random_state=42)),
    ])


def evaluate(pipeline, titles, labels, classes):
    predicted = pipeline.predict(titles)
    return {
        "size": len(titles),
        "f1_macro": round(float(f1_score(labels, predicted, average="macro")), 4),
        "accuracy": round(sum(p == y for p, y in zip(predicted, labels)) / len(labels), 4),
        "confusion_matrix": confusion_matrix(labels, predicted, labels=classes).tolist(),
        "classification_report": classification_report(labels, predicted, output_dict=True, zero_division=0),
        "errors": [{"title": t, "attendu": y, "predit": p} for t, y, p in zip(titles, labels, predicted) if p != y],
    }


def save_confusion_png(matrix, classes, f1, path):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib absent : pas d'image de matrice de confusion.")
        return
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(classes)), classes, rotation=45, ha="right")
    ax.set_yticks(range(len(classes)), classes)
    peak = max(max(row) for row in matrix) or 1
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            ax.text(j, i, value, ha="center", va="center", color="white" if value > peak / 2 else "black")
    ax.set_xlabel("Catégorie prédite")
    ax.set_ylabel("Catégorie réelle")
    ax.set_title(f"M2 · test groupé 80/20 · F1 macro = {f1:.2f} (données synthétiques)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Matrice de confusion : {path}")


def train_model():
    model_file = BASE_DIR / "m2_categorizer.joblib"
    metrics_file = BASE_DIR / "m2_metrics.json"

    original = load_csv("m2_categories.csv")
    extra = load_csv("m2_categories_extra.csv")
    real = load_csv("m2_real_titles.csv")
    data = original + extra
    titles = [t for t, _ in data]
    labels = [c for _, c in data]
    known = set(titles)
    groups = [base_phrase(t, known) for t in titles]
    classes = sorted(set(labels))
    print(f"{len(data)} titres étiquetés ({len(original)} d'origine + {len(extra)} ajoutés), "
          f"{len(set(groups))} phrases de base, {len(classes)} catégories.")

    # Ancienne méthode (découpage aléatoire) : gardée uniquement pour montrer la fuite
    x_tr, x_te, y_tr, y_te = train_test_split(titles, labels, test_size=0.2, random_state=42, stratify=labels)
    leaky = build_pipeline().fit(x_tr, y_tr)
    leaky_f1 = round(float(f1_score(y_te, leaky.predict(x_te), average="macro")), 4)

    # Découpage 80/20 groupé : une phrase et ses variantes préfixées restent du même côté
    splitter = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=42)
    train_idx, test_idx = next(splitter.split(titles, labels, groups))
    pipeline = build_pipeline()
    t0 = time.perf_counter()
    pipeline.fit([titles[i] for i in train_idx], [labels[i] for i in train_idx])
    train_duration = time.perf_counter() - t0
    test = evaluate(pipeline, [titles[i] for i in test_idx], [labels[i] for i in test_idx], classes)
    held_out = evaluate(pipeline, [t for t, _ in real], [c for _, c in real], classes)

    print(f"\nF1 macro, découpage aléatoire (fuite, à ne pas citer) : {leaky_f1:.4f}")
    print(f"F1 macro, test groupé 80/20 ({test['size']} titres)    : {test['f1_macro']:.4f} (objectif >= 0.80)")
    print(f"F1 macro, {held_out['size']} titres réalistes jamais vus     : {held_out['f1_macro']:.4f}")
    for error in held_out["errors"]:
        print(f"  ✗ {error['title']!r} : attendu {error['attendu']}, prédit {error['predit']}")

    # Modèle livré : réentraîné sur toutes les données étiquetées (les 40 titres réalistes restent hors entraînement)
    final = build_pipeline().fit(titles, labels)
    latencies = []
    for sample in ["Courses Monoprix", "Billet train SNCFT", "Loyer mensuel", "Places de cinema"]:
        started = time.perf_counter()
        final.predict([sample])
        latencies.append((time.perf_counter() - started) * 1000)
    avg_latency_ms = sum(latencies) / len(latencies)
    print(f"Latence moyenne d'inférence : {avg_latency_ms:.2f} ms (< 50 ms)")

    joblib.dump(final, model_file)
    print(f"\nModèle sérialisé sauvegardé dans : {model_file}")
    save_confusion_png(test["confusion_matrix"], classes, test["f1_macro"], BASE_DIR / "m2_confusion_matrix.png")

    metrics_data = {
        "dataset_size": len(data),
        "dataset_note": "Données synthétiques rédigées à la main (pas de vraies dépenses d'utilisateurs).",
        "base_phrases": len(set(groups)),
        "classes": classes,
        "split": "80/20 StratifiedGroupKFold groupé par phrase de base (random_state=42)",
        "f1_score_macro": test["f1_macro"],
        "target_f1_macro": 0.80,
        "f1_target_met": test["f1_macro"] >= 0.80,
        "f1_random_split_leaky": leaky_f1,
        "train_duration_s": round(train_duration, 3),
        "avg_latency_ms": round(avg_latency_ms, 2),
        "confusion_matrix": test["confusion_matrix"],
        "classification_report": test["classification_report"],
        "real_titles": {k: held_out[k] for k in ("size", "f1_macro", "accuracy", "confusion_matrix", "errors")},
    }
    with open(metrics_file, "w", encoding="utf-8") as f:
        json.dump(metrics_data, f, indent=2, ensure_ascii=False)
    print(f"Métriques sauvegardées dans : {metrics_file}")
    return test["f1_macro"]


if __name__ == "__main__":
    train_model()
