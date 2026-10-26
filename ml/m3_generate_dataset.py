"""M3 · Génère le jeu de démonstration du modèle de risque de retard (ml/m3_risk.csv).

Faute d'historique réel, les échéanciers sont SIMULÉS : chaque variable suit une loi
plausible et la cible (échéance payée avec plus de 3 jours de retard) est tirée selon
une relation logistique bruitée. Ce caractère généré est annoncé à la soutenance.

    python ml/m3_generate_dataset.py            # 600 lignes, graine 42
"""

import argparse
import csv
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from settlements.risk import FEATURES  # noqa: E402

# Effets « réels » utilisés pour simuler la cible (inconnus du modèle entraîné)
INTERCEPT = -4.2
EFFECTS = {
    "amount": 0.0012,
    "installments_count": 0.18,
    "past_late_count": 1.6,
    "avg_confirmation_delay_days": 0.55,
    "amount_weight_ratio": 0.0018,
    "days_since_creation": -0.01,
}
NOISE = 0.3


def simulate_row(rng: random.Random) -> dict:
    amount = round(min(max(rng.lognormvariate(5.4, 0.8), 20), 3000), 3)
    weight = rng.choice([1, 2, 3])
    row = {
        "amount": amount,
        "installments_count": rng.randint(2, 12),
        "past_late_count": min(int(rng.expovariate(1.2)), 8),
        "avg_confirmation_delay_days": round(min(rng.gammavariate(2.0, 1.1), 15), 2),
        "amount_weight_ratio": round(amount / weight, 3),
        "days_since_creation": rng.randint(0, 120),
    }
    z = INTERCEPT + sum(EFFECTS[name] * row[name] for name in FEATURES) + rng.gauss(0, NOISE)
    row["late"] = int(rng.random() < 1 / (1 + math.exp(-z)))
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default=str(ROOT / "ml" / "m3_risk.csv"))
    args = parser.parse_args()

    rng = random.Random(args.seed)
    rows = [simulate_row(rng) for _ in range(args.rows)]
    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*FEATURES, "late"])
        writer.writeheader()
        writer.writerows(rows)
    late = sum(row["late"] for row in rows)
    print(f"{len(rows)} lignes écrites dans {args.out} — {late} retards ({late / len(rows):.0%})")


if __name__ == "__main__":
    main()
