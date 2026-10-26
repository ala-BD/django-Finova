"""M3 · IA (1) : risque de retard de remboursement par régression logistique.

Le modèle est entraîné hors ligne par ml/m3_train_risk.py sur un jeu de démonstration
généré (ml/m3_generate_dataset.py) et sérialisé avec joblib dans ml/m3_risk_model.joblib.
Ce module ne dépend pas des modèles Django au chargement : le script d'entraînement
importe FEATURES d'ici, ce qui garantit le même ordre de variables des deux côtés.
"""

import time
from functools import lru_cache
from pathlib import Path

from django.conf import settings

FEATURES = [
    "amount",                      # montant de l'échéancier
    "installments_count",          # nombre d'échéances
    "past_late_count",             # retards passés (> 3 jours) du débiteur
    "avg_confirmation_delay_days", # délai moyen de confirmation de ses remboursements
    "amount_weight_ratio",         # montant / poids d'équité du débiteur (M1)
    "days_since_creation",         # jours écoulés depuis la création du remboursement
]
THRESHOLD = 0.5
DEFAULT_CONFIRMATION_DELAY = 2.0   # médiane du jeu de démonstration, si aucun historique


class RiskModelUnavailable(RuntimeError):
    """Le fichier du modèle est absent : lancer python ml/m3_train_risk.py."""


def model_path() -> Path:
    return Path(settings.BASE_DIR) / "ml" / "m3_risk_model.joblib"


@lru_cache(maxsize=1)
def load_model() -> dict:
    import joblib

    path = model_path()
    if not path.exists():
        raise RiskModelUnavailable(
            "Modèle de risque introuvable. Lancez « python ml/m3_train_risk.py »."
        )
    bundle = joblib.load(path)
    if bundle.get("features") != FEATURES:
        raise RiskModelUnavailable("Le modèle sérialisé ne correspond pas aux variables attendues.")
    return bundle


def predict_probability(features: dict) -> tuple[float, str, int]:
    """Retourne (probabilité de retard, version du modèle, latence en ms)."""
    bundle = load_model()
    started = time.perf_counter()
    row = [[float(features[name]) for name in FEATURES]]
    probability = float(bundle["pipeline"].predict_proba(row)[0][1])
    latency_ms = int((time.perf_counter() - started) * 1000)
    return probability, bundle["version"], latency_ms


FEATURE_LABELS = {
    "amount": "Montant à rembourser",
    "installments_count": "Nombre d'échéances",
    "past_late_count": "Retards passés du payeur",
    "avg_confirmation_delay_days": "Délai moyen de confirmation",
    "amount_weight_ratio": "Montant rapporté au poids d'équité",
    "days_since_creation": "Ancienneté du remboursement",
}


def explain(features: dict, bundle: dict | None = None) -> list[dict]:
    """Contribution de chaque variable au score (log-odds), par rapport au remboursement
    moyen du jeu d'entraînement : coefficient × valeur standardisée. Triée par importance."""
    bundle = bundle or load_model()
    scaler, model = bundle["pipeline"][0], bundle["pipeline"][-1]
    contributions = []
    for index, name in enumerate(FEATURES):
        standardized = (float(features[name]) - scaler.mean_[index]) / scaler.scale_[index]
        contributions.append({
            "feature": name,
            "label": FEATURE_LABELS[name],
            "value": features[name],
            "contribution": round(float(model.coef_[0][index] * standardized), 3),
        })
    contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
    largest = max((abs(item["contribution"]) for item in contributions), default=0) or 1
    for item in contributions:
        item["raises"] = item["contribution"] > 0
        item["share"] = round(abs(item["contribution"]) / largest * 100)
    return contributions


# ---------------------------------------------------------------------------
# Suggestion d'échéancier : on évalue tous les échéanciers possibles avec le modèle
# ---------------------------------------------------------------------------

PERIODS_PER_MONTH = {"weekly": 52 / 12, "biweekly": 26 / 12, "monthly": 1.0}
DAYS_PER_PERIOD = {"weekly": 7, "biweekly": 14, "monthly": 30}
CANDIDATE_COUNTS = range(2, 13)


def rank_plans(base_features: dict, monthly_capacity: float, bundle: dict | None = None) -> list[dict]:
    """Classe les échéanciers abordables (charge mensuelle ≤ capacité) du moins risqué au
    plus risqué ; à risque égal, le plus court d'abord. Fonction pure (sans base de données)."""
    bundle = bundle or load_model()
    amount = float(base_features["amount"])
    candidates = []
    for frequency, per_month in PERIODS_PER_MONTH.items():
        for count in CANDIDATE_COUNTS:
            installment = amount / count
            monthly_load = installment * per_month
            if monthly_load > monthly_capacity + 1e-9:
                continue
            candidates.append({
                "installments_count": count,
                "frequency": frequency,
                "installment_amount": round(installment, 3),
                "monthly_load": round(monthly_load, 3),
                "duration_days": DAYS_PER_PERIOD[frequency] * (count - 1),
            })
    if not candidates:
        return []
    rows = [
        [float({**base_features, "installments_count": c["installments_count"]}[name]) for name in FEATURES]
        for c in candidates
    ]
    for candidate, probability in zip(candidates, bundle["pipeline"].predict_proba(rows)[:, 1]):
        candidate["probability"] = round(float(probability), 4)
    candidates.sort(key=lambda c: (round(c["probability"], 3), c["duration_days"], c["installments_count"]))
    return candidates


def build_features(plan, today=None) -> dict:
    """Variables du débiteur (payeur du remboursement) calculées depuis la base."""
    features = settlement_features(plan.settlement, exclude_plan_id=plan.pk, today=today)
    features["installments_count"] = plan.installments_count
    return {name: features[name] for name in FEATURES}


def settlement_features(settlement, exclude_plan_id=None, today=None) -> dict:
    """Toutes les variables sauf installments_count, qui dépend de l'échéancier choisi."""
    from django.db.models import Avg, DurationField, ExpressionWrapper, F
    from django.utils import timezone

    from .models import Installment, Settlement

    today = today or timezone.localdate()
    debtor = settlement.payer

    past_installments = Installment.objects.filter(
        plan__settlement__payer__user_id=debtor.user_id,
    ).exclude(plan_id=exclude_plan_id)
    past_late_count = sum(1 for inst in past_installments if inst.is_late(today))

    delay = (
        Settlement.objects.filter(
            payer__user_id=debtor.user_id,
            status=Settlement.Status.CONFIRMED,
            confirmed_at__isnull=False,
        )
        .annotate(
            delay=ExpressionWrapper(F("confirmed_at") - F("created_at"), output_field=DurationField())
        )
        .aggregate(avg=Avg("delay"))["avg"]
    )
    avg_delay = delay.total_seconds() / 86400 if delay is not None else DEFAULT_CONFIRMATION_DELAY

    amount = float(settlement.amount)
    return {
        "amount": round(amount, 3),
        "past_late_count": past_late_count,
        "avg_confirmation_delay_days": round(avg_delay, 2),
        "amount_weight_ratio": round(amount / float(debtor.weight), 3),
        "days_since_creation": (today - timezone.localtime(settlement.created_at).date()).days,
    }
