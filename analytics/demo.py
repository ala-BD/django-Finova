"""
Jeu de dépenses de démonstration (données générées, annoncées comme telles).

Le même générateur sert au tableau de bord (tant que M2 n'est pas livré) et à
l'évaluation de la détection : on y injecte des anomalies et des doublons connus,
puis on mesure précision et rappel (commande `analytics_evaluate`).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from analytics.datasource import ExpenseRecord

CATEGORIES = [
    "Alimentation",
    "Transport",
    "Logement",
    "Loisirs",
    "Santé",
    "Shopping",
    "Factures",
    "Autre",
]

# Libellés typiques par catégorie : (libellé, montant médian en TND).
LABELS = {
    "Alimentation": [("Courses Carrefour", 70), ("Marché central", 40), ("Restaurant", 55), ("Monoprix", 60)],
    "Transport": [("Taxi", 12), ("Carburant", 60), ("Louage", 20), ("Ticket métro", 8)],
    "Logement": [("Produits ménagers", 35), ("Plombier", 80), ("Petit électroménager", 90)],
    "Loisirs": [("Cinéma", 18), ("Café entre amis", 15), ("Concert", 45), ("Bowling", 30)],
    "Santé": [("Pharmacie", 30), ("Consultation médecin", 60)],
    "Shopping": [("Vêtements", 90), ("Chaussures", 110), ("Librairie", 35)],
    "Factures": [("Recharge téléphone", 20), ("Facture STEG", 75), ("Facture SONEDE", 40)],
    "Autre": [("Cadeau", 60), ("Divers", 25)],
}
CATEGORY_WEIGHTS = [30, 18, 6, 14, 6, 10, 10, 6]

# Dépenses récurrentes : (libellé, catégorie, montant fixe, jour du mois).
RECURRING = [
    ("Loyer", "Logement", Decimal("900.000"), 1),
    ("Abonnement internet", "Factures", Decimal("69.000"), 5),
]

TYPO_VARIANTS = [
    lambda title: title,
    lambda title: title.lower(),
    lambda title: f"{title} (bis)",
    lambda title: title[:-1] if len(title) > 6 else title,
]


@dataclass(frozen=True)
class DemoGroup:
    id: int
    name: str
    currency: str
    members: tuple[str, ...]
    share: float
    rate_to_tnd: Decimal = Decimal("1")
    recurring: bool = False


@dataclass
class DemoDataset:
    records: list[ExpenseRecord]
    anomaly_ids: set[int] = field(default_factory=set)
    duplicate_ids: set[int] = field(default_factory=set)


def generate_dataset(
    *,
    today: date,
    groups: list[DemoGroup],
    total: int,
    anomalies: int,
    duplicates: int,
    months: int = 6,
    seed: int = 42,
) -> DemoDataset:
    """Génère exactement `total` dépenses, dont `anomalies` et `duplicates` injectées."""
    rng = random.Random(seed)
    start = _months_back(today, months - 1)
    span_days = (today - start).days

    drafts: list[dict] = []

    for group in groups:
        if not group.recurring:
            continue
        month = start
        while month <= today:
            for title, category, amount, day in RECURRING:
                when = month.replace(day=day)
                if when <= today:
                    drafts.append(
                        _draft(group, title, category, _to_group_currency(amount, group), when, group.members[0])
                    )
            month = _next_month(month)

    n_random = total - len(drafts) - anomalies - duplicates
    if n_random < 0:
        raise ValueError("total trop petit pour les dépenses récurrentes et injectées")

    shares = [g.share for g in groups]
    for _ in range(n_random):
        group = rng.choices(groups, weights=shares)[0]
        category = rng.choices(CATEGORIES, weights=CATEGORY_WEIGHTS)[0]
        title, typical = rng.choice(LABELS[category])
        amount = Decimal(typical) * Decimal(str(math.exp(rng.gauss(0, 0.3))))
        drafts.append(
            _draft(
                group,
                title,
                category,
                _to_group_currency(amount, group),
                start + timedelta(days=rng.randint(0, span_days)),
                rng.choice(group.members),
            )
        )

    # Anomalies : montant ×5 à ×10 du montant habituel de la catégorie dans le groupe.
    typical_amount = {}
    for draft in drafts:
        typical_amount.setdefault((draft["group"].id, draft["category"]), []).append(draft["amount"])
    anomaly_drafts = []
    for _ in range(anomalies):
        group = rng.choices(groups, weights=shares)[0]
        category = rng.choices(CATEGORIES, weights=CATEGORY_WEIGHTS)[0]
        amounts = typical_amount.get((group.id, category))
        if not amounts:
            amounts = [_to_group_currency(Decimal(LABELS[category][0][1]), group)]
        title, _ = rng.choice(LABELS[category])
        factor = Decimal(str(rng.uniform(5, 10)))
        anomaly_drafts.append(
            _draft(
                group,
                title,
                category,
                median(amounts) * factor,
                start + timedelta(days=rng.randint(0, span_days)),
                rng.choice(group.members),
            )
        )

    # Doublons : même payeur, même montant, date à ± 1 jour, libellé proche.
    originals = rng.sample(drafts, duplicates)
    duplicate_pairs = []
    for original in originals:
        when = min(original["date"] + timedelta(days=rng.randint(0, 1)), today)
        copy = dict(original, title=rng.choice(TYPO_VARIANTS)(original["title"]), date=when)
        duplicate_pairs.append((original, copy))

    all_drafts = drafts + anomaly_drafts + [copy for _, copy in duplicate_pairs]
    rng.shuffle(all_drafts)
    all_drafts.sort(key=lambda d: d["date"])
    for index, draft in enumerate(all_drafts, start=1):
        draft["id"] = index

    records = [_to_record(d) for d in all_drafts]
    duplicate_ids = {
        max((original, copy), key=lambda d: (d["date"], d["id"]))["id"]
        for original, copy in duplicate_pairs
    }
    return DemoDataset(
        records=records,
        anomaly_ids={d["id"] for d in anomaly_drafts},
        duplicate_ids=duplicate_ids,
    )


def dashboard_dataset(*, today: date, user_name: str) -> DemoDataset:
    """Jeu affiché dans le tableau de bord tant que M2 n'est pas livré."""
    groups = [
        DemoGroup(1, "Colocation Lafayette", "TND", (user_name, "Sami", "Mariem"), share=0.7, recurring=True),
        DemoGroup(2, "Voyage à Djerba", "TND", (user_name, "Yahya", "Linda", "Imem"), share=0.2),
        DemoGroup(3, "Week-end à Paris", "EUR", (user_name, "Wassim"), share=0.1, rate_to_tnd=Decimal("3.4")),
    ]
    return generate_dataset(today=today, groups=groups, total=240, anomalies=5, duplicates=4, seed=2026)


def evaluation_dataset(*, today: date, seed: int = 7) -> DemoDataset:
    """Protocole du rapport : 500 dépenses, 30 anomalies et 20 doublons injectés."""
    groups = [
        DemoGroup(1, "Colocation", "TND", ("Ala", "Sami", "Mariem", "Yahya"), share=0.75, recurring=True),
        DemoGroup(2, "Voyage", "TND", ("Linda", "Imem", "Zahra", "Wassim"), share=0.25),
    ]
    return generate_dataset(today=today, groups=groups, total=500, anomalies=30, duplicates=20, seed=seed)


def _draft(group, title, category, amount, when, payer):
    return {
        "group": group,
        "title": title,
        "category": category,
        "amount": Decimal(amount).quantize(Decimal("0.001")),
        "date": when,
        "payer": payer,
    }


def _to_record(draft) -> ExpenseRecord:
    group = draft["group"]
    return ExpenseRecord(
        id=draft["id"],
        group_id=group.id,
        group_name=group.name,
        title=draft["title"],
        amount=draft["amount"],
        currency=group.currency,
        date=draft["date"],
        category=draft["category"],
        payer=draft["payer"],
    )


def _to_group_currency(amount_tnd: Decimal, group: DemoGroup) -> Decimal:
    return (Decimal(amount_tnd) / group.rate_to_tnd).quantize(Decimal("0.001"))


def _months_back(day: date, months: int) -> date:
    year, month = day.year, day.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)
