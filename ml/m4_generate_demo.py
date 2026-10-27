"""
M4 (Linda) · Génère le jeu de démonstration utilisé pour évaluer la prévision.

Faute de données réelles, on simule 14 mois de dépenses d'une colocation sur
six catégories, avec des comportements réalistes :
- dépenses fixes à date connue (loyer le 1er, factures autour du 8, 12 et 15) ;
- achats fréquents au fil du mois (alimentation, transport, restaurants, loisirs) ;
- mois « chargés » ou « calmes » (facteur d'humeur), tendance légère et
  saisonnalité (factures d'électricité plus élevées l'été et l'hiver).

Le jeu est annoncé comme généré à la soutenance. Graine fixe : reproductible.

    python ml/m4_generate_demo.py            → ml/data/m4_demo_expenses.csv
"""

import calendar
import csv
import random
from datetime import date
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "data" / "m4_demo_expenses.csv"
START = date(2025, 8, 1)
MONTHS = 14
SEED = 2026

FREQUENT = {
    # catégorie : (achats par mois, montant moyen)
    "Alimentation": (14, 42.0),
    "Restaurants": (7, 38.0),
    "Transport": (12, 13.0),
    "Loisirs": (4, 36.0),
}


def month_iter(start, count):
    year, month = start.year, start.month
    for _ in range(count):
        yield date(year, month, 1)
        month += 1
        if month > 12:
            year, month = year + 1, 1


def generate(seed=SEED):
    rng = random.Random(seed)
    rows = []
    for index, month in enumerate(month_iter(START, MONTHS)):
        last_day = calendar.monthrange(month.year, month.month)[1]
        mood = rng.uniform(0.75, 1.3)
        trend = 1 + 0.01 * index
        season = 1.35 if month.month in (1, 2, 7, 8) else 1.0

        def add(day, category, amount, label):
            rows.append(
                {
                    "date": date(month.year, month.month, day).isoformat(),
                    "category": category,
                    "label": label,
                    "amount": f"{amount:.3f}",
                }
            )

        add(1, "Logement", 1200.0, "Loyer")
        add(8, "Factures", rng.uniform(80, 130) * season * mood, "STEG")
        add(12, "Factures", rng.uniform(25, 45), "SONEDE")
        add(15, "Factures", 69.9, "Internet")

        for category, (count, average) in FREQUENT.items():
            # Le nombre d'achats varie avec l'humeur du mois.
            purchases = max(1, round(count * mood * rng.uniform(0.85, 1.15)))
            for _ in range(purchases):
                amount = rng.uniform(0.4, 1.6) * average * mood * trend
                add(rng.randint(1, last_day), category, amount, category.lower())
    rows.sort(key=lambda row: (row["date"], row["category"]))
    return rows


def main():
    rows = generate(SEED)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "category", "label", "amount"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} dépenses générées sur {MONTHS} mois → {OUTPUT}")


if __name__ == "__main__":
    main()
