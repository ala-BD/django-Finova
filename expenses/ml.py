"""
Module IA / ML pour les dépenses (Module M2 - Yahya).
Charge le classifieur scikit-learn (ml/m2_categorizer.joblib)
et fournit les prédictions de catégorie en temps réel.
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

import joblib
from django.conf import settings

from budgets.models import Category
from groups.models import Group

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_PATH = Path(settings.BASE_DIR) / "ml" / "m2_categorizer.joblib"

# En dessous de ce score, la suggestion est affichée mais la catégorie n'est pas présélectionnée
MIN_CONFIDENCE = 0.35


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


# Mappage des labels du modèle vers des noms de catégories en français courants.
# Le premier nom est celui proposé quand le groupe n'a pas encore la catégorie.
LABEL_MAPPING = {
    "alimentation": ["Alimentation", "Nourriture", "Courses", "Restaurant", "Resto", "Repas"],
    "transport": ["Transport", "Déplacements", "Voyage", "Carburant", "Essence", "Train"],
    "logement": ["Logement", "Loyer", "Maison", "Charges", "Hébergement", "Bricolage"],
    "loisirs": ["Loisirs", "Sorties", "Divertissement", "Culture", "Sport"],
    "sante": ["Santé", "Médical", "Pharmacie", "Soins"],
    "shopping": ["Shopping", "Vêtements", "Achats", "Mode", "Cadeaux"],
    "factures": ["Factures", "Abonnements", "Services", "Internet", "Téléphone"],
    "autre": ["Autre", "Divers", "Imprévus"],
}
LABELS = list(LABEL_MAPPING)


def display_name(label: str) -> str:
    return LABEL_MAPPING.get(label, ["Autre"])[0]


def _fold(text: str) -> str:
    """Minuscules sans accents : « Santé » et « sante » se comparent."""
    text = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").strip()


def label_from_name(name: str) -> Optional[str]:
    """« Pharmacie » → « sante » ; None si le nom ne correspond à aucun label connu."""
    folded = _fold(name)
    for label, names in LABEL_MAPPING.items():
        if folded == label or folded in {_fold(n) for n in names}:
            return label
    return None


def _keyword_label(lower: str) -> str:
    # Repli heuristique par mots-clés simples si le modèle est indisponible
    if any(w in lower for w in ["courses", "carrefour", "monoprix", "pizza", "burger", "resto", "pain", "repas", "cafe"]):
        return "alimentation"
    if any(w in lower for w in ["train", "taxi", "uber", "essence", "peage", "avion", "bus", "metro", "louage"]):
        return "transport"
    if any(w in lower for w in ["loyer", "steg", "sonede", "electricite", "meuble", "peinture", "serrure"]):
        return "logement"
    if any(w in lower for w in ["cinema", "netflix", "spotify", "soiree", "concert", "bowling", "ski"]):
        return "loisirs"
    if any(w in lower for w in ["pharmacie", "medecin", "dentiste", "doliprane", "kine", "pansement"]):
        return "sante"
    if any(w in lower for w in ["zara", "nike", "vetement", "jean", "chaussure", "fnac", "shopping"]):
        return "shopping"
    if any(w in lower for w in ["internet", "forfait", "mobile", "abonnement", "salle de sport", "facture"]):
        return "factures"
    return "autre"


def predict_category(title: str) -> Tuple[str, float, str]:
    """Label normalisé (ex : 'alimentation'), confiance entre 0 et 1, source ('modele' ou 'mots-cles')."""
    text = (title or "").strip()[:200]
    if not text:
        return "autre", 0.0, "vide"
    model = get_model()
    if model is not None:
        try:
            probs = model.predict_proba([text])[0]
            best = max(range(len(probs)), key=probs.__getitem__)
            return str(model.classes_[best]).lower(), float(probs[best]), "modele"
        except Exception as e:
            logger.warning("Prédiction M2 impossible, repli mots-clés : %s", e)
    label = _keyword_label(_fold(text))
    return label, (0.5 if label != "autre" else 0.2), "mots-cles"


def predict_category_label(title: str) -> str:
    """Prédit le libellé normalisé de la catégorie (ex: 'alimentation')."""
    return predict_category(title)[0]


def match_group_category(label: str, group: Optional[Group]) -> Optional[Category]:
    """Catégorie du groupe correspondant au label (nom exact puis alias), sinon None.

    Jamais de « première catégorie venue » : une mauvaise catégorie présélectionnée est pire
    qu'aucune, l'utilisateur ne la remarque pas.
    """
    if group is None:
        return None
    categories = list(Category.objects.filter(group=group))
    aliases = [_fold(name) for name in LABEL_MAPPING.get(label, [label])]
    for alias in aliases:
        for category in categories:
            if _fold(category.name) == alias:
                return category
    for alias in aliases:
        for category in categories:
            if alias in _fold(category.name):
                return category
    return None


def suggest_category(title: str, group: Optional[Group] = None) -> dict:
    """Suggestion complète pour l'interface : catégorie existante ou nom à créer."""
    label, confidence, source = predict_category(title)
    category = match_group_category(label, group)
    return {
        "label": label,
        "confidence": round(confidence, 2),
        "confident": confidence >= MIN_CONFIDENCE and source != "vide",
        "source": source,
        "category_id": category.id if category else None,
        "category_name": category.name if category else display_name(label),
        "exists": category is not None,
    }


def predict_category_for_group(title: str, group: Optional[Group] = None) -> Tuple[Optional[Category], float]:
    """
    Retourne la catégorie (instance de Category) correspondante pour un groupe donné
    ainsi que le score de confiance estimé (entre 0.0 et 1.0). None si le groupe ne l'a pas.
    """
    label, confidence, _ = predict_category(title)
    return match_group_category(label, group), round(confidence, 2)
