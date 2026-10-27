"""
M4 (Linda) · Entraîne le modèle de risque de dépassement de budget.

Question posée au modèle : « au jour d, ce budget sera-t-il dépassé à la fin du mois ? »
Sortie : une probabilité entre 0 et 1, affichée dans l'application (« Risque 78 % »).

Protocole :
1. 60 colocations simulées avec ml/m4_generate_demo.py (une graine par groupe),
   chacune avec ses propres limites de budget (moyenne des 3 premiers mois × 0,85 à 1,30).
2. Une ligne par (groupe, catégorie, mois, jour de contrôle), à partir du 4e mois :
   variables calculées comme en production (budgets.forecasting.overrun_features),
   en ne regardant que le passé ; cible = total réel du mois > limite.
3. Séparation PAR GROUPE : 45 groupes pour l'entraînement, 15 jamais vus pour le test.
4. Choix du modèle ET du seuil de décision par validation croisée (GroupKFold)
   sur l'entraînement uniquement : régression logistique contre gradient boosting.
5. Évaluation finale sur le test, comparée à deux règles simples :
   « déjà 80 % consommés » et « prévision > limite ».
6. Fourchette de prévision : quantiles 10 % / 90 % du rapport réel / prévu,
   par période du mois, appris sur l'entraînement et vérifiés sur le test.

Sorties :
- ml/models/m4_overrun_model.joblib   (modèle + variables + quantiles + métriques)
- ml/results/m4_overrun_metrics.json et ml/results/m4_overrun_report.md

    python ml/m4_train_overrun.py
"""

import calendar
import json
import random
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupKFold, cross_val_predict, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml"))

from budgets.forecasting import (  # noqa: E402
    OVERRUN_FEATURES,
    day_bucket,
    forecast_month_total,
    overrun_features,
)
from m4_generate_demo import generate  # noqa: E402

MODEL_PATH = ROOT / "ml" / "models" / "m4_overrun_model.joblib"
RESULTS = ROOT / "ml" / "results"
MODEL_VERSION = "m4-overrun-1.0"
GROUPS = 60
TEST_GROUPS = 15
CHECKPOINTS = (3, 6, 9, 12, 15, 18, 21, 24, 27)
FIRST_EVALUATED_MONTH = 3
HISTORY_MONTHS = 6
SEED = 7


def to_daily(rows):
    daily = defaultdict(lambda: defaultdict(float))
    for row in rows:
        day = date.fromisoformat(row["date"])
        daily[(row["category"], day.replace(day=1))][day.day] += float(row["amount"])
    return daily


def days_in(month):
    return calendar.monthrange(month.year, month.month)[1]


def build_rows(group_id, daily, rng):
    categories = sorted({category for category, _ in daily})
    months = sorted({month for _, month in daily})
    total = {key: sum(days.values()) for key, days in daily.items()}

    def spent_until(category, month, day):
        return sum(v for d, v in daily.get((category, month), {}).items() if d <= day)

    def group_spent_until(month, day):
        return sum(spent_until(category, month, day) for category in categories)

    rows = []
    for category in categories:
        first_totals = [total.get((category, month), 0.0) for month in months[:3]]
        limit = mean(first_totals) * rng.uniform(0.85, 1.30)
        if limit <= 0:
            continue
        for index in range(FIRST_EVALUATED_MONTH, len(months)):
            month = months[index]
            history = months[max(0, index - HISTORY_MONTHS):index]
            history_totals = [total.get((category, past), 0.0) for past in history]
            actual = total.get((category, month), 0.0)
            n_days = days_in(month)
            days = daily.get((category, month), {})
            for checkpoint in CHECKPOINTS:
                so_far = [days.get(day, 0.0) for day in range(1, checkpoint + 1)]
                spent = sum(so_far)
                history_cum = [spent_until(category, past, checkpoint) for past in history]
                forecast = float(
                    forecast_month_total(so_far, n_days, history_totals, history_cum).predicted_total
                )
                features = overrun_features(
                    spent,
                    forecast,
                    limit,
                    checkpoint,
                    n_days,
                    history_totals,
                    history_cum,
                    group_spent=group_spent_until(month, checkpoint),
                    group_usual_spent=mean(group_spent_until(past, checkpoint) for past in history),
                )
                rows.append(
                    {
                        "group": group_id,
                        "features": [features[name] for name in OVERRUN_FEATURES],
                        "target": int(actual > limit),
                        "forecast": forecast,
                        "spent": spent,
                        "actual": actual,
                        "limit": limit,
                        "bucket": day_bucket(features["day_fraction"]),
                    }
                )
    return rows


def classification_metrics(y_true, y_pred, y_score=None):
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    result = {
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 3),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 3),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 3),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 3),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }
    if y_score is not None:
        result["roc_auc"] = round(float(roc_auc_score(y_true, y_score)), 3)
    return result


def learn_quantiles(rows):
    ratios = defaultdict(list)
    for row in rows:
        if row["forecast"] > 0:
            ratios[row["bucket"]].append(row["actual"] / row["forecast"])
    return {
        bucket: (round(float(np.quantile(values, 0.10)), 4), round(float(np.quantile(values, 0.90)), 4))
        for bucket, values in ratios.items()
    }


def interval_coverage(rows, quantiles):
    inside = 0
    for row in rows:
        low_ratio, high_ratio = quantiles[row["bucket"]]
        low = max(row["forecast"] * low_ratio, row["spent"])
        high = max(row["forecast"] * high_ratio, low)
        inside += low - 1e-6 <= row["actual"] <= high + 1e-6
    return round(inside / len(rows), 3)


def main():
    rng = random.Random(SEED)
    rows = []
    for group_id in range(GROUPS):
        rows += build_rows(group_id, to_daily(generate(seed=1000 + group_id)), rng)

    test_ids = set(range(GROUPS - TEST_GROUPS, GROUPS))
    train = [row for row in rows if row["group"] not in test_ids]
    test = [row for row in rows if row["group"] in test_ids]
    x_train = np.array([row["features"] for row in train])
    y_train = np.array([row["target"] for row in train])
    g_train = np.array([row["group"] for row in train])
    x_test = np.array([row["features"] for row in test])
    y_test = np.array([row["target"] for row in test])

    candidates = {
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
        "gradient_boosting": GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.05, random_state=SEED
        ),
    }
    cv = GroupKFold(n_splits=5)
    cv_scores = {
        name: round(float(cross_val_score(model, x_train, y_train, groups=g_train, cv=cv, scoring="f1").mean()), 3)
        for name, model in candidates.items()
    }
    best_name = max(cv_scores, key=cv_scores.get)
    out_of_fold = cross_val_predict(
        candidates[best_name], x_train, y_train, groups=g_train, cv=cv, method="predict_proba"
    )[:, 1]
    threshold = round(
        float(max(np.arange(0.2, 0.81, 0.02), key=lambda t: f1_score(y_train, out_of_fold >= t))), 2
    )
    model = candidates[best_name].fit(x_train, y_train)

    scores = model.predict_proba(x_test)[:, 1]
    predictions = (scores >= threshold).astype(int)
    spent_index = OVERRUN_FEATURES.index("spent_ratio")
    forecast_index = OVERRUN_FEATURES.index("forecast_ratio")
    rule_80 = (x_test[:, spent_index] >= 0.8).astype(int)
    rule_forecast = (x_test[:, forecast_index] > 1.0).astype(int)

    started = time.perf_counter()
    for row in x_test[:200]:
        model.predict_proba(row.reshape(1, -1))
    latency_ms = (time.perf_counter() - started) * 1000 / 200

    quantiles = learn_quantiles(train)
    by_period = {}
    for bucket in ("debut", "milieu", "fin"):
        index = [i for i, row in enumerate(test) if row["bucket"] == bucket]
        by_period[bucket] = {
            "model_f1": round(float(f1_score(y_test[index], predictions[index])), 3),
            "rule_forecast_f1": round(float(f1_score(y_test[index], rule_forecast[index])), 3),
        }
    importances = (
        model.feature_importances_
        if hasattr(model, "feature_importances_")
        else np.abs(model[-1].coef_[0]) / np.abs(model[-1].coef_[0]).sum()
    )

    metrics = {
        "model_version": MODEL_VERSION,
        "chosen_model": best_name,
        "cv_f1": cv_scores,
        "threshold": threshold,
        "dataset": {
            "groups": GROUPS,
            "train_groups": GROUPS - TEST_GROUPS,
            "test_groups": TEST_GROUPS,
            "train_rows": len(train),
            "test_rows": len(test),
            "overrun_rate_test": round(float(y_test.mean()), 3),
            "note": "Colocations simulées (ml/m4_generate_demo.py), annoncées comme données générées.",
        },
        "test": {
            "model": classification_metrics(y_test, predictions, scores),
            "rule_spent_80": classification_metrics(y_test, rule_80),
            "rule_forecast_over_limit": classification_metrics(
                y_test, rule_forecast, x_test[:, forecast_index]
            ),
        },
        "by_period": by_period,
        "feature_importance": {
            name: round(float(value), 3) for name, value in zip(OVERRUN_FEATURES, importances)
        },
        "interval": {"quantiles": quantiles, "coverage_test": interval_coverage(test, quantiles), "target": 0.8},
        "latency_ms": round(latency_ms, 3),
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "features": OVERRUN_FEATURES,
            "quantiles": quantiles,
            "threshold": threshold,
            "version": MODEL_VERSION,
            "metrics": metrics,
        },
        MODEL_PATH,
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "m4_overrun_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(metrics)

    model_metrics = metrics["test"]["model"]
    print(f"modèle retenu : {best_name} (F1 en validation croisée : {cv_scores})")
    for name, values in metrics["test"].items():
        print(f"{name:26s} précision={values['precision']:.3f} rappel={values['recall']:.3f} F1={values['f1']:.3f}")
    print(f"ROC-AUC {model_metrics['roc_auc']:.3f} · couverture fourchette {metrics['interval']['coverage_test']:.0%} · {latency_ms:.2f} ms")
    print(f"→ {MODEL_PATH}")


LABELS = {
    "model": "**Modèle IA**",
    "rule_spent_80": "Règle « déjà 80 % consommés »",
    "rule_forecast_over_limit": "Règle « prévision > limite »",
}


def write_report(metrics):
    test = metrics["test"]
    matrix = test["model"]["confusion_matrix"]
    lines = [
        "# M4 · Risque de dépassement de budget — résultats",
        "",
        f"Modèle retenu : **{metrics['chosen_model'].replace('_', ' ')}** "
        f"(F1 en validation croisée par groupe : {', '.join(f'{k.replace(chr(95), chr(32))} {v:.3f}' for k, v in metrics['cv_f1'].items())}).",
        "",
        f"Jeu : {metrics['dataset']['groups']} colocations simulées — {metrics['dataset']['train_rows']} lignes "
        f"d'entraînement ({metrics['dataset']['train_groups']} groupes), {metrics['dataset']['test_rows']} lignes de test "
        f"({metrics['dataset']['test_groups']} groupes jamais vus). Taux de dépassement réel dans le test : "
        f"{metrics['dataset']['overrun_rate_test']:.0%}.",
        "",
        "> Données générées (aucune donnée réelle disponible) : annoncé à la soutenance.",
        "",
        f"## Test (seuil {metrics['threshold']:.2f}, choisi par validation croisée)",
        "",
        "| Méthode | Précision | Rappel | F1 | Exactitude |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, values in test.items():
        lines.append(
            f"| {LABELS[name]} | {values['precision']:.3f} | {values['recall']:.3f} | {values['f1']:.3f} | {values['accuracy']:.3f} |"
        )
    lines += [
        "",
        f"ROC-AUC : **{test['model']['roc_auc']:.3f}** pour le modèle, "
        f"{test['rule_forecast_over_limit']['roc_auc']:.3f} pour la prévision seule (score = prévision / limite).",
        "",
        "## F1 selon le moment du mois",
        "",
        "| Période | Modèle IA | Règle « prévision > limite » |",
        "|---|---:|---:|",
        *[
            f"| {name} | {values['model_f1']:.3f} | {values['rule_forecast_f1']:.3f} |"
            for name, values in metrics["by_period"].items()
        ],
        "",
        "Le gain est le plus fort en début de mois, quand l'alerte est la plus utile.",
        "",
        "## Matrice de confusion du modèle",
        "",
        "| | Prédit : pas de dépassement | Prédit : dépassement |",
        "|---|---:|---:|",
        f"| **Réel : pas de dépassement** | {matrix['tn']} (vrais négatifs) | {matrix['fp']} (fausses alertes) |",
        f"| **Réel : dépassement** | {matrix['fn']} (dépassements manqués) | {matrix['tp']} (vrais positifs) |",
        "",
        "## Importance des variables",
        "",
        "| Variable | Importance |",
        "|---|---:|",
    ]
    for name, value in sorted(metrics["feature_importance"].items(), key=lambda item: -item[1]):
        lines.append(f"| `{name}` | {value:.3f} |")
    interval = metrics["interval"]
    lines += [
        "",
        "## Fourchette de prévision (80 %)",
        "",
        f"Couverture sur le test : **{interval['coverage_test']:.0%}** des totaux réels tombent dans la fourchette (cible 80 %).",
        "",
        "## Temps de réponse",
        "",
        f"Prédiction : {metrics['latency_ms']:.2f} ms par budget (cible < 200 ms).",
        "",
        "## Limites (à dire à l'oral)",
        "",
        "- Données générées : dans le simulateur, un mois « chargé » touche toutes les catégories, "
        "ce qui rend `group_pace_vs_usual` très utile. Hypothèse plausible (invités, fêtes), à confirmer sur données réelles.",
        "- Le modèle ne remplace pas la prévision : il la complète par une probabilité, plus fine qu'un oui/non.",
        "",
    ]
    (RESULTS / "m4_overrun_report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
