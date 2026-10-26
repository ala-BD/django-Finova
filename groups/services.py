from decimal import Decimal

from core.rounding import largest_remainder_rounding

from .models import Group, Membership

INCOME_WEIGHTS = {
    "student": Decimal("1.000"),
    "active": Decimal("2.000"),
    "senior": Decimal("3.000"),
}

SPLIT_RULE_HELP = {
    Group.SplitRule.EQUAL: "Chaque membre paie la même part.",
    Group.SplitRule.BY_WEIGHT: (
        "Chaque membre paie selon son poids (étudiant 1, actif 2, actif senior 3). "
        "Exemple : 90 TND avec 1, 2 et 3 → 15 / 30 / 45."
    ),
    Group.SplitRule.BY_CONSUMPTION: (
        "Chacun paie ce qu'il a consommé sur le ticket (M2). "
        "Tant qu'il n'y a pas de lignes de ticket, la répartition égale s'applique."
    ),
}


def is_member(user, group) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return group.memberships.filter(user=user, is_active=True).exists()


def is_admin(user, group) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return group.memberships.filter(
        user=user,
        is_active=True,
        role=Membership.Role.ADMIN,
    ).exists()


def default_weight_for(user) -> Decimal:
    profile = getattr(user, "income_profile", "active")
    return INCOME_WEIGHTS.get(profile, Decimal("1.000"))


def split_by_weight(amount, weights: dict) -> dict:
    amount = Decimal(str(amount))
    total = sum(Decimal(str(weight)) for weight in weights.values())
    if total <= 0:
        raise ValueError("La somme des poids doit être strictement positive.")
    raw = {member: amount * Decimal(str(weight)) / total for member, weight in weights.items()}
    return largest_remainder_rounding(raw, amount, places=3)


def split_equally(amount, member_ids: list) -> dict:
    amount = Decimal(str(amount))
    if not member_ids:
        return {}
    share = amount / len(member_ids)
    raw = {member_id: share for member_id in member_ids}
    return largest_remainder_rounding(raw, amount, places=3)


def consumption_totals(group) -> dict | None:
    """Totaux consommés par membre (M2). None si le module dépenses n'est pas prêt."""
    from django.apps import apps
    from django.core.exceptions import FieldError
    from django.db.models import Sum

    try:
        ItemClaim = apps.get_model("expenses", "ItemClaim")
    except LookupError:
        return None
    try:
        rows = (
            ItemClaim.objects.filter(item__expense__group=group)
            .values("user_id")
            .annotate(total=Sum("share_amount"))
        )
    except (FieldError, AttributeError):
        return None
    totals = {row["user_id"]: Decimal(str(row["total"] or 0)) for row in rows}
    if not totals or all(value == 0 for value in totals.values()):
        return None
    return totals


def preview_split(group, amount) -> dict:
    members = list(group.active_memberships())
    member_ids = [membership.user_id for membership in members]
    amount = Decimal(str(amount))
    note = SPLIT_RULE_HELP.get(group.default_split_rule, "")
    used_rule = group.default_split_rule

    if group.default_split_rule == Group.SplitRule.BY_WEIGHT:
        weights = {membership.user_id: membership.weight for membership in members}
        parts = split_by_weight(amount, weights)
    elif group.default_split_rule == Group.SplitRule.BY_CONSUMPTION:
        totals = consumption_totals(group)
        if totals:
            parts = split_by_weight(amount, totals)
        else:
            parts = split_equally(amount, member_ids)
            used_rule = Group.SplitRule.EQUAL
            note = (
                "Aucune consommation de ticket (M2) pour l'instant : "
                "répartition égale en repli."
            )
    else:
        parts = split_equally(amount, member_ids)

    rows = [
        {
            "membership": membership,
            "share": parts.get(membership.user_id, Decimal("0")),
            "weight": membership.weight,
        }
        for membership in members
    ]
    return {
        "amount": amount,
        "rows": rows,
        "note": note,
        "used_rule": used_rule,
    }


def get_member_balance(user, group) -> Decimal:
    try:
        from settlements.services import compute_balances

        balances = compute_balances(group)
    except (NotImplementedError, ImportError):
        return Decimal("0")
    if user in balances:
        return Decimal(str(balances[user]))
    if user.pk in balances:
        return Decimal(str(balances[user.pk]))
    return Decimal(str(balances.get(str(user.pk), 0)))


def can_leave(user, group) -> tuple[bool, str]:
    if get_member_balance(user, group) != 0:
        return False, "Impossible de quitter le groupe tant que votre solde n'est pas à zéro."
    membership = group.memberships.filter(user=user, is_active=True).first()
    if (
        membership
        and membership.role == Membership.Role.ADMIN
        and group.admin_count() <= 1
    ):
        return False, "Le dernier admin ne peut pas quitter le groupe."
    return True, ""


# ---------------------------------------------------------------------------
# Partie IA — assistant de création de groupe (M1)
# ---------------------------------------------------------------------------

import json as _json
import re as _re

_VALID_CURRENCIES = {c[0] for c in Group.Currency.choices}
_VALID_RULES = {r[0] for r in Group.SplitRule.choices}

_SYSTEM_PROMPT = """Tu es un assistant financier pour l'application Finova.
L'utilisateur décrit son projet de groupe de partage de dépenses en une phrase.

AVANT tout, évalue si la description est un vrai projet de partage de dépenses :
- Projets VALIDES : voyage, colocation, événement, repas entre amis, camping, fête, mariage, sport collectif, location de voiture, activité de groupe, etc.
- Projets INVALIDES : mots sans sens ("test", "bonjour", "azerty"), sujets sans lien avec des dépenses partagées, phrases trop courtes ou absurdes.

Si la description N'EST PAS un projet de partage de dépenses valide, réponds UNIQUEMENT :
{"error": "La description ne correspond pas à un projet de partage de dépenses. Décrivez un voyage, une colocation, un événement ou toute activité impliquant des dépenses communes."}

Si la description EST valide, génère UNIQUEMENT un objet JSON avec ces champs :
{
  "name": "Nom du groupe (3 à 80 caractères)",
  "description": "Description courte du groupe",
  "currency": "TND",
  "default_split_rule": "equal",
  "categories": ["Catégorie1", "Catégorie2"],
  "budgets": [{"category": "Catégorie1", "amount": 500}]
}
Règles pour les projets valides :
- currency : TND, EUR, USD ou MAD uniquement
- default_split_rule : equal, by_weight ou by_consumption uniquement
- categories : liste de 2 à 6 chaînes non vides
- budgets : liste d'objets {category, amount} où amount > 0
- Réponds UNIQUEMENT avec le JSON, rien d'autre.
"""


def _extract_json(text: str) -> dict:
    """Extrait le premier objet JSON valide dans le texte du LLM."""
    # Cherche un bloc ```json ... ``` ou { ... }
    match = _re.search(r"```json\s*(\{.*?\})\s*```", text, _re.DOTALL)
    if not match:
        match = _re.search(r"(\{.*\})", text, _re.DOTALL)
    if not match:
        raise ValueError("Aucun objet JSON trouvé dans la réponse du LLM.")
    return _json.loads(match.group(1))


def _validate_group_json(data: dict) -> dict:
    """
    Valide et normalise le JSON retourné par le LLM.
    - Si le LLM retourne {"error": "..."}, lève ValueError avec ce message (rejet sémantique).
    - Lève ValueError avec un message clair si un champ est invalide.
    """
    # Rejet sémantique : le LLM a décidé que le prompt n'est pas un projet valide
    if "error" in data:
        raise ValueError(str(data["error"]))

    errors = []

    name = str(data.get("name", "")).strip()
    if len(name) < 3 or len(name) > 80:
        errors.append("name doit contenir entre 3 et 80 caractères.")

    currency = str(data.get("currency", "TND")).strip().upper()
    if currency not in _VALID_CURRENCIES:
        currency = "TND"

    rule = str(data.get("default_split_rule", "equal")).strip()
    if rule not in _VALID_RULES:
        rule = "equal"

    categories = data.get("categories", [])
    if not isinstance(categories, list) or len(categories) < 1:
        errors.append("categories doit être une liste non vide.")
    else:
        categories = [str(c).strip() for c in categories if str(c).strip()]
        if not categories:
            errors.append("categories contient uniquement des chaînes vides.")

    budgets = data.get("budgets", [])
    if not isinstance(budgets, list):
        budgets = []
    valid_budgets = []
    for b in budgets:
        if isinstance(b, dict) and "category" in b and "amount" in b:
            try:
                amount = float(b["amount"])
                if amount > 0:
                    valid_budgets.append({
                        "category": str(b["category"]).strip(),
                        "amount": amount,
                    })
            except (TypeError, ValueError):
                pass

    if errors:
        raise ValueError(" | ".join(errors))

    return {
        "name": name,
        "description": str(data.get("description", "")).strip(),
        "currency": currency,
        "default_split_rule": rule,
        "categories": categories,
        "budgets": valid_budgets,
    }


def generate_group_from_prompt(prompt: str, user=None, max_retries: int = 2) -> dict:
    """
    Génère un groupe depuis une description en langage naturel via le LLM.

    - Appelle core.ai.call_llm (Google Gemini)
    - Extrait et valide le JSON retourné
    - Réessaie jusqu'à max_retries fois en cas de JSON invalide
    - Journalise chaque tentative dans AIRequestLog (via call_llm)

    Retourne un dict validé :
        {name, description, currency, default_split_rule, categories, budgets}

    Lève ValueError si le JSON reste invalide après tous les essais.
    Lève core.ai.LLMError si l'API est injoignable.
    """
    from core.ai import LLMError, call_llm

    full_prompt = f"{_SYSTEM_PROMPT}\n\nDescription du projet : {prompt.strip()}"
    last_error = None

    for attempt in range(1, max_retries + 2):  # 1 essai + max_retries
        try:
            raw_text = call_llm(
                feature="m1.generate_group",
                prompt=full_prompt,
                module="groups",
                user=user,
            )
            data = _extract_json(raw_text)
            return _validate_group_json(data)
        except LLMError:
            raise  # Erreur réseau → on ne réessaie pas
        except (ValueError, _json.JSONDecodeError) as exc:
            last_error = exc
            if attempt <= max_retries:
                # Demande au LLM de corriger son JSON
                full_prompt = (
                    f"{_SYSTEM_PROMPT}\n\nDescription du projet : {prompt.strip()}\n\n"
                    f"Tentative précédente invalide ({exc}). Corrige et renvoie uniquement le JSON."
                )
            continue

    raise ValueError(
        f"Le LLM n'a pas produit un JSON valide après {max_retries + 1} tentatives. "
        f"Dernière erreur : {last_error}"
    )
