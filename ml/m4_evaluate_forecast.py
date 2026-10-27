"""
M4 (Linda) · Validation temporelle de la prévision de fin de mois.

Protocole (aucune fuite d'information du futur) :
- pour chaque mois M à partir du 3e (au moins 2 mois d'historique) ;
- pour chaque catégorie et chaque point de contrôle d (jours 5, 10, 15, 20, 25) ;
- on ne donne au modèle QUE les dépenses des mois < M et celles de M jusqu'au jour d ;
- on compare la prévision au vrai total de M.

Méthodes comparées :
- naïve        : moyenne historique des totaux mensuels (référence imposée) ;
- régression   : régression linéaire du cumul du mois en cours, extrapolée ;
- mélange      : moyenne de la régression et du profil historique ;
- Finova (M4)  : budgets/forecasting.py, c'est-à-dire le profil historique
                 (déjà dépensé + reste habituel des mois précédents) dès qu'il
                 y a au moins 2 mois d'historique.

Sorties : ml/results/m4_forecast_metrics.json et ml/results/m4_forecast_report.md

    python ml/m4_generate_demo.py
    python ml/m4_evaluate_forecast.py
"""

import calendar
import csv
import json
import math
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from budgets.forecasting import (  # noqa: E402
    daily_rate,
    forecast_month_total,
    historical_remaining,
    naive_forecast,
)

DATA = ROOT / "ml" / "data" / "m4_demo_expenses.csv"
RESULTS = ROOT / "ml" / "results"
CHECKPOINTS = (5, 10, 15, 20, 25)
MIN_HISTORY = 2


def load(path):
    """daily[(catégorie, mois)][jour] = montant du jour."""
    daily = defaultdict(lambda: defaultdict(float))
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            day = date.fromisoformat(row["date"])
            daily[(row["category"], day.replace(day=1))][day.day] += float(row["amount"])
    return daily


def days_in(month):
    return calendar.monthrange(month.year, month.month)[1]


def series(daily, category, month, until):
    days = daily.get((category, month), {})
    return [days.get(day, 0.0) for day in range(1, until + 1)]


def total(daily, category, month):
    return sum(daily.get((category, month), {}).values())


def cumulative(daily, category, month, until):
    return sum(series(daily, category, month, min(until, days_in(month))))


def metrics(errors):
    return {
        "mae": round(mean(abs(e) for e in errors), 3),
        "rmse": round(math.sqrt(mean(e * e for e in errors)), 3),
    }


def evaluate(daily):
    categories = sorted({category for category, _ in daily})
    months = sorted({month for _, month in daily})
    errors = defaultdict(list)
    by_checkpoint = defaultdict(lambda: defaultdict(list))
    by_category = defaultdict(lambda: defaultdict(list))
    latencies = []
    forecast_month_total([1.0, 2.0, 3.0], 30)  # échauffement : import de scikit-learn hors mesure

    for index, month in enumerate(months):
        if index < MIN_HISTORY:
            continue
        history = months[:index]
        n_days = days_in(month)
        for category in categories:
            actual = total(daily, category, month)
            history_totals = [total(daily, category, past) for past in history]
            for checkpoint in CHECKPOINTS:
                so_far = series(daily, category, month, checkpoint)
                spent = sum(so_far)
                history_cum = [cumulative(daily, category, past, checkpoint) for past in history]

                started = time.perf_counter()
                model = float(
                    forecast_month_total(so_far, n_days, history_totals, history_cum).predicted_total
                )
                latencies.append((time.perf_counter() - started) * 1000)

                pace = daily_rate(so_far) * (n_days - checkpoint)
                profile = historical_remaining(history_totals, history_cum, n_days - checkpoint, n_days)
                predictions = {
                    "naive": float(naive_forecast(history_totals)),
                    "pace": spent + pace,
                    "blend": spent + (pace + profile) / 2,
                    "finova": model,
                }
                for name, prediction in predictions.items():
                    error = prediction - actual
                    errors[name].append(error)
                    by_checkpoint[checkpoint][name].append(error)
                    by_category[category][name].append(error)

    summary = {name: metrics(values) for name, values in errors.items()}
    naive_mae = summary["naive"]["mae"]
    for name in summary:
        summary[name]["gain_vs_naive_pct"] = round(100 * (naive_mae - summary[name]["mae"]) / naive_mae, 1)

    latencies.sort()
    return {
        "dataset": {
            "file": str(DATA.relative_to(ROOT)).replace("\\", "/"),
            "months": len(months),
            "evaluated_months": len(months) - MIN_HISTORY,
            "categories": categories,
            "checkpoints": list(CHECKPOINTS),
            "predictions": len(errors["finova"]),
            "note": "Jeu de démonstration généré (ml/m4_generate_demo.py), annoncé comme tel.",
        },
        "overall": summary,
        "by_checkpoint": {
            str(day): {name: metrics(values) for name, values in methods.items()}
            for day, methods in sorted(by_checkpoint.items())
        },
        "by_category": {
            category: {name: metrics(values) for name, values in methods.items()}
            for category, methods in sorted(by_category.items())
        },
        "latency_ms": {
            "mean": round(mean(latencies), 3),
            "p95": round(latencies[int(0.95 * (len(latencies) - 1))], 3),
            "max": round(latencies[-1], 3),
        },
    }


LABELS = {
    "naive": "Naïve (moyenne historique)",
    "pace": "Régression linéaire du mois en cours",
    "blend": "Mélange régression + profil",
    "finova": "**Finova M4 (profil historique)**",
}


def write_report(results):
    overall = results["overall"]
    lines = [
        "# M4 · Prévision de fin de mois — résultats",
        "",
        f"Jeu : `{results['dataset']['file']}` — {results['dataset']['months']} mois, "
        f"{len(results['dataset']['categories'])} catégories, "
        f"{results['dataset']['predictions']} prévisions évaluées "
        f"(points de contrôle aux jours {', '.join(map(str, results['dataset']['checkpoints']))}).",
        "",
        "> Données générées (aucune donnée réelle disponible) : annoncé à la soutenance.",
        "",
        "## Validation temporelle (tous points de contrôle)",
        "",
        "| Méthode | MAE (TND) | RMSE (TND) | Gain MAE vs naïve |",
        "|---|---:|---:|---:|",
    ]
    for name in ("naive", "pace", "blend", "finova"):
        row = overall[name]
        lines.append(f"| {LABELS[name]} | {row['mae']:.2f} | {row['rmse']:.2f} | {row['gain_vs_naive_pct']:+.1f} % |")

    lines += ["", "## MAE selon le jour de la prévision", "", "| Jour | Naïve | Finova M4 |", "|---:|---:|---:|"]
    for day, methods in results["by_checkpoint"].items():
        lines.append(f"| {day} | {methods['naive']['mae']:.2f} | {methods['finova']['mae']:.2f} |")

    lines += ["", "## MAE par catégorie", "", "| Catégorie | Naïve | Finova M4 |", "|---|---:|---:|"]
    for category, methods in results["by_category"].items():
        lines.append(f"| {category} | {methods['naive']['mae']:.2f} | {methods['finova']['mae']:.2f} |")

    latency = results["latency_ms"]
    target_gain = overall["finova"]["gain_vs_naive_pct"]
    lines += [
        "",
        "## Temps de réponse (calcul de la prévision, hors base de données)",
        "",
        f"Moyenne {latency['mean']:.2f} ms · p95 {latency['p95']:.2f} ms · max {latency['max']:.2f} ms (cible < 1 s).",
        "",
        "## Lecture",
        "",
        f"- Cible du rapport : MAE modèle < MAE naïve et gain ≥ 10 % → **{target_gain:+.1f} %** "
        f"({'atteinte' if target_gain >= 10 else 'non atteinte'}).",
        "- La naïve ignore ce qui a déjà été dépensé ce mois-ci : elle se trompe dès que le mois est inhabituel.",
        "- La régression linéaire extrapole le rythme : elle surestime les catégories à dépense ponctuelle "
        "(loyer le 1er, factures le 8), d'où une erreur pire que la naïve. Elle ne sert donc que de repli "
        "quand il n'existe aucun historique.",
        "- Le profil historique combine le réel déjà dépensé et le reste habituel : c'est la méthode retenue.",
        "- Plus la prévision est faite tard dans le mois, plus elle est précise (voir le tableau par jour).",
        "",
    ]
    (RESULTS / "m4_forecast_report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    if not DATA.exists():
        sys.exit("Jeu absent : lancer d'abord python ml/m4_generate_demo.py")
    results = evaluate(load(DATA))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "m4_forecast_metrics.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_report(results)
    for name in ("naive", "pace", "blend", "finova"):
        row = results["overall"][name]
        print(f"{name:8s} MAE={row['mae']:8.2f}  RMSE={row['rmse']:8.2f}  gain={row['gain_vs_naive_pct']:+6.1f} %")
    print(f"latence moyenne {results['latency_ms']['mean']:.2f} ms, p95 {results['latency_ms']['p95']:.2f} ms")
    print(f"→ {RESULTS / 'm4_forecast_report.md'}")


if __name__ == "__main__":
    main()
