"""
Chargement du modèle de risque de dépassement (entraîné par ml/m4_train_overrun.py).

Le fichier joblib est lu une seule fois par processus (cache). S'il est absent
ou illisible, l'application continue sans probabilité : l'IA n'est jamais bloquante.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL_PATH = Path(settings.BASE_DIR) / "ml" / "models" / "m4_overrun_model.joblib"


@lru_cache(maxsize=1)
def load_overrun_model() -> dict | None:
    try:
        import joblib

        bundle = joblib.load(MODEL_PATH)
    except Exception as exc:  # fichier absent, version de scikit-learn incompatible…
        logger.warning("Modèle de risque M4 indisponible (%s).", exc)
        return None
    return bundle


def predict_overrun_probability(features: dict) -> float | None:
    bundle = load_overrun_model()
    if bundle is None:
        return None
    row = [[features[name] for name in bundle["features"]]]
    return float(bundle["model"].predict_proba(row)[0][1])


def model_card() -> dict | None:
    """Version, seuil et métriques du modèle, affichés dans l'onglet « Analyse IA »."""
    bundle = load_overrun_model()
    if bundle is None:
        return None
    return {"version": bundle["version"], "threshold": bundle["threshold"], **bundle["metrics"]}
