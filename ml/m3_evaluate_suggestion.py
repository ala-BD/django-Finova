"""M3 · Mesure de la suggestion d'échéancier (IA 4).

Pour chaque remboursement du jeu de test (20 % de ml/m3_risk.csv, même découpage que
l'entraînement), on tire une capacité de paiement mensuelle plausible, puis on compare le
risque prédit de l'échéancier suggéré avec deux choix « sans IA » :
  - par défaut : 3 mensualités (si le payeur peut les assumer) ;
  - étalement maximal : 12 mensualités, le réflexe « payer le moins possible par mois ».

Limite assumée : le gain est mesuré avec le modèle de risque lui-même (jeu généré), pas
sur des retards réels observés.

    python ml/m3_train_risk.py
    python ml/m3_evaluate_suggestion.py
"""

import csv
import json
import random
import statistics
import sys
import time
from pathlib import Path

import joblib
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from settlements.risk import FEATURES, THRESHOLD, rank_plans  # noqa: E402

OUT = ROOT / "ml" / "m3_suggestion_metrics.json"


def probability_of(candidates, count, frequency="monthly"):
    for candidate in candidates:
        if candidate["installments_count"] == count and candidate["frequency"] == frequency:
            return candidate["probability"]
    return None


def main():
    bundle = joblib.load(ROOT / "ml" / "m3_risk_model.joblib")
    with open(ROOT / "ml" / "m3_risk.csv", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    labels = [int(row["late"]) for row in rows]
    _, test_rows = train_test_split(rows, test_size=0.2, stratify=labels, random_state=42)

    rng = random.Random(42)
    results, latencies = [], []
    for row in test_rows:
        base = {name: float(row[name]) for name in FEATURES if name != "installments_count"}
        capacity = base["amount"] / rng.uniform(1.5, 8)  # le payeur rembourse en 1,5 à 8 mois
        started = time.perf_counter()
        ranked = rank_plans(base, capacity, bundle=bundle)
        latencies.append((time.perf_counter() - started) * 1000)
        if not ranked:
            continue
        everything = rank_plans(base, float("inf"), bundle=bundle)
        default = probability_of(ranked, 3)  # None si 3 mensualités dépassent la capacité
        results.append({
            "suggested": ranked[0]["probability"],
            "default": default,
            "spread": probability_of(everything, 12),
        })

    def summary(key):
        pairs = [(r["suggested"], r[key]) for r in results if r[key] is not None]
        return {
            "cases": len(pairs),
            "mean_risk_baseline": round(statistics.mean(b for _, b in pairs), 3),
            "mean_risk_suggested": round(statistics.mean(s for s, _ in pairs), 3),
            "mean_reduction_points": round(statistics.mean(b - s for s, b in pairs) * 100, 1),
            "high_risk_baseline": sum(b >= THRESHOLD for _, b in pairs),
            "high_risk_suggested": sum(s >= THRESHOLD for s, _ in pairs),
            "never_worse": all(s <= b + 1e-9 for s, b in pairs),
        }

    metrics = {
        "test_cases": len(test_rows),
        "feasible_cases": len(results),
        "vs_default_3_monthly": summary("default"),
        "vs_max_spread_12_monthly": summary("spread"),
        "latency_ms": {"mean": round(statistics.mean(latencies), 2),
                       "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 2)},
        "note": "Gain mesuré avec le modèle de risque (jeu de démonstration généré).",
    }
    OUT.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{len(results)}/{len(test_rows)} remboursements avec au moins un échéancier abordable")
    for key, title in (("vs_default_3_monthly", "3 mensualités"), ("vs_max_spread_12_monthly", "12 mensualités")):
        m = metrics[key]
        print(
            f"vs {title:15} ({m['cases']} cas) : risque moyen {m['mean_risk_baseline']:.0%} → "
            f"{m['mean_risk_suggested']:.0%} (−{m['mean_reduction_points']} pts), "
            f"risque élevé {m['high_risk_baseline']} → {m['high_risk_suggested']}, jamais pire : {m['never_worse']}"
        )
    print(f"Latence : moyenne {metrics['latency_ms']['mean']} ms, p95 {metrics['latency_ms']['p95']} ms")


if __name__ == "__main__":
    main()
