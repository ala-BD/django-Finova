"""
IA M5 : détection d'anomalies (score robuste MAD) et de doublons (difflib).

Les montants doivent être exprimés dans une même devise (voir currency.convert_records).
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from statistics import median

from analytics.models import AnomalyFlag

ANOMALY_THRESHOLD = 3.5  # score robuste d'Iglewicz et Hoaglin
MIN_CATEGORY_SIZE = 8
MIN_LABEL_SIZE = 5
DUPLICATE_SIMILARITY = 0.8
DUPLICATE_MAX_DAYS = 1


@dataclass(frozen=True)
class Detection:
    record: object
    kind: str
    score: float
    reason: str


def robust_scores(amounts: list[Decimal]) -> list[float]:
    """Score robuste : 0,6745 × (x − médiane) / MAD."""
    values = [float(a) for a in amounts]
    med = median(values)
    mad = median(abs(v - med) for v in values)
    # MAD nulle (montants tous identiques, ex. un loyer) : plancher à 10 % de la médiane.
    mad = max(mad, 0.1 * abs(med), 0.001)
    return [0.6745 * (v - med) / mad for v in values]


def detect_anomalies(records, threshold=ANOMALY_THRESHOLD, min_count=MIN_CATEGORY_SIZE) -> list[Detection]:
    """
    Dépenses anormalement élevées par rapport à leur catégorie, dans leur groupe.

    Une catégorie mélange des libellés aux montants différents (ticket de métro et
    carburant) : si le libellé est fréquent, le score doit aussi dépasser le seuil
    parmi les dépenses de même libellé, ce qui élimine les faux positifs « normaux ».
    """
    by_category = defaultdict(list)
    by_label = defaultdict(list)
    for record in records:
        by_category[(record.group_id, record.category)].append(record)
        by_label[(record.group_id, _normalize(record.title))].append(record)

    label_scores = {}
    for items in by_label.values():
        if len(items) >= MIN_LABEL_SIZE:
            label_scores.update(zip((r.id for r in items), robust_scores([r.amount for r in items])))

    detections = []
    for (_, category), items in by_category.items():
        if len(items) < min_count:
            continue
        med = median(float(r.amount) for r in items)
        for record, score in zip(items, robust_scores([r.amount for r in items])):
            if score > threshold and label_scores.get(record.id, score) > threshold:
                ratio = float(record.amount) / med if med else 0
                detections.append(
                    Detection(
                        record=record,
                        kind=AnomalyFlag.Kind.ANOMALY,
                        score=round(score, 2),
                        reason=f"{ratio:.1f} fois le montant médian de la catégorie {category}".replace(".", ","),
                    )
                )
    return sorted(detections, key=lambda d: d.score, reverse=True)


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def detect_duplicates(
    records, similarity=DUPLICATE_SIMILARITY, max_days=DUPLICATE_MAX_DAYS
) -> list[Detection]:
    """Même groupe, même payeur, même montant, date à ± 1 jour et libellé proche."""
    buckets = defaultdict(list)
    for record in records:
        buckets[(record.group_id, record.payer, record.amount)].append(record)

    flagged = {}
    for items in buckets.values():
        if len(items) < 2:
            continue
        items.sort(key=lambda r: (r.date, r.id))
        for i, first in enumerate(items):
            for second in items[i + 1:]:
                if (second.date - first.date).days > max_days:
                    break
                ratio = title_similarity(first.title, second.title)
                if ratio >= similarity and second.id not in flagged:
                    flagged[second.id] = Detection(
                        record=second,
                        kind=AnomalyFlag.Kind.DUPLICATE,
                        score=round(ratio, 2),
                        reason=f"Ressemble à « {first.title} » du {first.date:%d/%m/%Y} (même payeur, même montant)",
                    )
    return sorted(flagged.values(), key=lambda d: (d.record.date, d.record.id), reverse=True)


def run_detection(records) -> tuple[list[Detection], int]:
    """Renvoie (détections, durée en ms)."""
    started = time.perf_counter()
    detections = detect_anomalies(records) + detect_duplicates(records)
    return detections, int((time.perf_counter() - started) * 1000)


def sync_flags(user, detections) -> int:
    """Enregistre les détections ; conserve l'état « revue » des signalements existants."""
    created = 0
    for detection in detections:
        record = detection.record
        _, was_created = AnomalyFlag.objects.update_or_create(
            user=user,
            expense_ref=record.id,
            kind=detection.kind,
            defaults={
                "expense_label": f"{record.title} · {record.group_name}"[:200],
                "expense_amount": record.amount,
                "currency": record.currency,
                "expense_date": record.date,
                "score": detection.score,
                "reason": detection.reason[:255],
            },
        )
        created += int(was_created)
    return created


def precision_recall(predicted: set[int], expected: set[int]) -> dict:
    true_positives = len(predicted & expected)
    precision = true_positives / len(predicted) if predicted else 0.0
    recall = true_positives / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "true_positives": true_positives,
        "false_positives": len(predicted - expected),
        "false_negatives": len(expected - predicted),
    }


def _normalize(title: str) -> str:
    return " ".join(title.lower().split())
