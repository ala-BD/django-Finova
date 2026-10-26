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


def build_features(plan, today=None) -> dict:
    """Variables du débiteur (payeur du remboursement) calculées depuis la base."""
    from django.db.models import Avg, DurationField, ExpressionWrapper, F
    from django.utils import timezone

    from .models import Installment, Settlement

    today = today or timezone.localdate()
    settlement = plan.settlement
    debtor = settlement.payer

    past_installments = Installment.objects.filter(
        plan__settlement__payer__user_id=debtor.user_id,
    ).exclude(plan=plan)
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

    amount = float(plan.total_amount)
    return {
        "amount": round(amount, 3),
        "installments_count": plan.installments_count,
        "past_late_count": past_late_count,
        "avg_confirmation_delay_days": round(avg_delay, 2),
        "amount_weight_ratio": round(amount / float(debtor.weight), 3),
        "days_since_creation": (today - timezone.localtime(settlement.created_at).date()).days,
    }
