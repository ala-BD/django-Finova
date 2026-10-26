"""
Module IA / ML pour les dépenses (Module M2 - Yahya).
Charge le classifieur scikit-learn (ml/m2_categorizer.joblib)
et fournit les prédictions de catégorie en temps réel.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import joblib
from django.conf import settings

from budgets.models import Category
from groups.models import Group

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_PATH = Path(settings.BASE_DIR) / "ml" / "m2_categorizer.joblib"


def get_model():
    """Charge le modèle sérialisé en mémoire (singleton)."""
    global _MODEL
    if _MODEL is None:
        if _MODEL_PATH.exists():
            try:
                _MODEL = joblib.load(_MODEL_PATH)
            except Exception as e:
                logger.warning("Impossible de charger le modèle ML M2 : %s", e)
    return _MODEL


# Mappage des labels du modèle vers des noms de catégories en français courants
LABEL_MAPPING = {
    "alimentation": ["Alimentation", "Nourriture", "Courses", "Restaurant", "Repas"],
    "transport": ["Transport", "Déplacements", "Voyage", "Carburant", "Train"],
    "logement": ["Logement", "Loyer", "Maison", "Charges", "Bricolage"],
    "loisirs": ["Loisirs", "Sorties", "Divertissement", "Culture", "Sport"],
    "sante": ["Santé", "Médical", "Pharmacie", "Soins"],
    "shopping": ["Shopping", "Vêtements", "Achats", "Mode", "Cadeaux"],
    "factures": ["Factures", "Abonnements", "Services", "Internet"],
    "autre": ["Autre", "Divers", "Imprévus"],
}


def predict_category_label(title: str) -> str:
    """Prédit le libellé normalisé de la catégorie (ex: 'alimentation')."""
    if not title or not title.strip():
        return "autre"

    model = get_model()
    if model is not None:
        try:
            pred = model.predict([title.strip()])[0]
            return str(pred).lower()
        except Exception:
            pass

    # Repli heuristique par mots-clés simples si le modèle est indisponible
    lower = title.lower()
    if any(w in lower for w in ["courses", "carrefour", "monoprix", "pizza", "burger", "resto", "pain", "repas", "cafe"]):
        return "alimentation"
    if any(w in lower for w in ["train", "taxi", "uber", "essence", "peage", "avion", "bus", "metro"]):
        return "transport"
    if any(w in lower for w in ["loyer", "steg", "eau", "sonede", "electricite", "meuble", "peinture", "serrure"]):
        return "logement"
    if any(w in lower for w in ["cinema", "netflix", "spotify", "bar", "soiree", "concert", "bowling", "ski"]):
        return "loisirs"
    if any(w in lower for w in ["pharmacie", "medecin", "dentiste", "doliprane", "kine", "lunettes", "pansement"]):
        return "sante"
    if any(w in lower for w in ["zara", "nike", "vetement", "jean", "chaussure", "fnac", "shopping"]):
        return "shopping"
    if any(w in lower for w in ["internet", "forfait", "mobile", "abonnement", "salle de sport", "facture"]):
        return "factures"

    return "autre"


def predict_category_for_group(title: str, group: Optional[Group] = None) -> Tuple[Optional[Category], float]:
    """
    Retourne la catégorie (instance de Category) correspondante pour un groupe donné
    ainsi que le score de confiance estimé (entre 0.0 et 1.0).
    """
    predicted_label = predict_category_label(title)

    # Calcul du score de confiance si le modèle supporte predict_proba
    confidence = 0.85
    model = get_model()
    if model is not None and hasattr(model, "predict_proba"):
        try:
            probs = model.predict_proba([title.strip()])[0]
            confidence = float(max(probs))
        except Exception:
            confidence = 0.85

    # Recherche de la catégorie correspondante dans la base de données
    possible_names = LABEL_MAPPING.get(predicted_label, [predicted_label.capitalize()])
    queryset = Category.objects.all()
    if group is not None:
        queryset = queryset.filter(group=group) | queryset.filter(group__isnull=True)

    matched_cat = None
    # 1. Correspondance exacte ou partielle sur les alias
    for name in possible_names:
        matched_cat = queryset.filter(name__iexact=name).first()
        if matched_cat:
            break
        matched_cat = queryset.filter(name__icontains=name).first()
        if matched_cat:
            break

    # 2. Si aucune correspondance, prendre la première catégorie ou None
    if not matched_cat:
        matched_cat = queryset.first()

    return matched_cat, round(confidence, 2)
