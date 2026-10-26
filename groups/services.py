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


# ---------------------------------------------------------------------------
# Partie IA — charte du groupe (M1)
# ---------------------------------------------------------------------------

CHARTER_BASE_RULES = {
    "coloc": [
        "Chaque dépense commune (loyer, courses, factures) est saisie dans Finova le jour même.",
        "Les factures récurrentes (loyer, internet, électricité) sont enregistrées comme dépenses récurrentes.",
        "Les courses partagées sont gardées sur un ticket : chacun réclame ce qu'il consomme.",
        "On prévient le groupe avant toute dépense commune importante.",
    ],
    "voyage": [
        "Les réservations (transport, hébergement) sont saisies dès qu'elles sont payées.",
        "Les dépenses du quotidien sont ajoutées le soir même, avec le ticket si possible.",
        "Chacun peut avancer de l'argent : les comptes sont soldés au retour, en un minimum de virements.",
        "Les dépenses personnelles (souvenirs, extras) ne passent pas par le groupe.",
    ],
    "evenement": [
        "Le budget de l'événement est fixé et annoncé avant les premiers achats.",
        "Chaque achat est validé par l'organisateur puis saisi avec son justificatif.",
        "Les comptes sont soldés dans la semaine qui suit l'événement.",
    ],
    "association": [
        "Toute dépense est rattachée à une catégorie et accompagnée d'un justificatif.",
        "Les dépenses importantes sont validées par un administrateur avant d'être engagées.",
        "Un point sur les soldes est fait à chaque réunion.",
    ],
    "famille": [
        "Les dépenses communes (courses, sorties, cadeaux) sont saisies au fur et à mesure.",
        "La participation de chacun tient compte de ses moyens grâce aux poids.",
        "On en parle simplement en cas de désaccord sur une dépense.",
    ],
    "autre": [
        "Chaque dépense commune est saisie rapidement dans Finova.",
        "Les comptes sont soldés régulièrement, en un minimum de virements.",
    ],
}
COMMON_RULES = [
    "Un remboursement n'est compté qu'une fois confirmé par la personne qui le reçoit.",
    "Personne ne quitte le groupe tant que son solde n'est pas à zéro.",
    "On reste bienveillant : un oubli se règle par un simple rappel.",
]
CHARTER_MAX_LENGTH = 4000


def _key_rules_list(key_rules: str) -> list[str]:
    parts = _re.split(r"[\n;•]+|\.\s+", key_rules or "")
    rules = []
    for part in parts:
        part = part.strip(" -.*").strip()
        if part:
            rules.append(part[0].upper() + part[1:] + ("" if part.endswith((".", "!", "?")) else "."))
    return rules[:6]


def charter_template(group, group_type: str, key_rules: str) -> str:
    """Charte rédigée sans IA (repli) : règles clés de l'admin, règles du type, règles communes."""
    split_rule = Group.SplitRule(group.default_split_rule).label.lower()
    rules = (
        _key_rules_list(key_rules)
        + CHARTER_BASE_RULES.get(group_type, CHARTER_BASE_RULES["autre"])
        + [f"Les dépenses sont partagées selon la règle « {split_rule} » ; elle peut être changée pour une dépense précise."]
        + COMMON_RULES
    )
    lines = [f"### Charte de « {group.name} »", ""]
    lines += [f"{index}. {rule}" for index, rule in enumerate(rules, 1)]
    return "\n".join(lines)


def _clean_charter(text: str) -> str:
    text = _re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", (text or "").strip())
    return text.strip()


def generate_charter(group, group_type: str, key_rules: str, user=None) -> dict:
    """Charte proposée par le LLM, validée (non vide, bornée, avec une liste de règles),
    sinon modèle de texte. Retourne {content, source: llm|template, latency_ms}. Ne lève jamais."""
    import time

    from core.ai import LLMError, call_llm

    from .models import GroupCharter

    started = time.perf_counter()
    type_label = dict(GroupCharter.GroupType.choices).get(group_type, "Autre")
    prompt = (
        "Rédige en français la charte (règles de vie) d'un groupe de partage de dépenses.\n"
        f"Nom : {group.name}\nType : {type_label}\nDevise : {group.currency}\n"
        f"Règle de partage actuelle : {Group.SplitRule(group.default_split_rule).label}\n"
        f"Nombre de membres : {group.active_memberships().count()}\n"
        f"Règles clés voulues par l'administrateur (à intégrer fidèlement) : {key_rules or 'aucune'}\n"
        "Format : Markdown, un titre « ### Charte de « nom » », puis 6 à 9 règles numérotées, courtes, concrètes "
        "et bienveillantes (saisie des dépenses, remboursements et délais, confirmation par le bénéficiaire, "
        "désaccords). Aucun préambule ni conclusion, pas de montant inventé, 1 800 caractères maximum."
    )
    try:
        content = _clean_charter(call_llm("m1.charter", prompt, module="groups", user=user))
        if not content or len(content) > CHARTER_MAX_LENGTH or not _re.search(r"^\s*(\d+[.)]|[-*])\s", content, _re.M):
            raise LLMError("Charte générée invalide (vide, trop longue ou sans liste de règles).")
        source = "llm"
    except LLMError:
        content = charter_template(group, group_type, key_rules)
        source = "template"
    return {"content": content, "source": source, "latency_ms": int((time.perf_counter() - started) * 1000)}


# ---------------------------------------------------------------------------
# Partie IA — coach d'équité (M1) : chiffres et recommandation calculés, l'IA explique
# ---------------------------------------------------------------------------

COACH_MAX_MEMBERS = 12


def equity_comparison(amount, members: list[dict]) -> dict:
    """Parts de chacun selon la règle égale et selon le poids, au millime (somme exacte)."""
    amount = Decimal(str(amount))
    keys = list(range(len(members)))
    equal = split_equally(amount, keys)
    weighted = split_by_weight(amount, {k: Decimal(str(members[k]["weight"])) for k in keys})
    rows = [
        {
            "name": members[k]["name"],
            "weight": _decimal_text(members[k]["weight"]),
            "equal": str(equal[k]),
            "by_weight": str(weighted[k]),
            "difference": str(weighted[k] - equal[k]),
        }
        for k in keys
    ]
    return {"amount": str(amount), "rows": rows}


def _decimal_text(value) -> str:
    text = f"{Decimal(str(value)):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def recommend_rule(members: list[dict]) -> dict:
    """Recommandation déterministe et explicable : profils identiques → règle égale ;
    écart de moyens entre membres → règle par poids."""
    weights = [Decimal(str(m["weight"])) for m in members]
    ratio = max(weights) / min(weights)
    if ratio == 1:
        return {
            "rule": Group.SplitRule.EQUAL.value,
            "label": Group.SplitRule.EQUAL.label,
            "reasons": ["tous les membres ont le même poids : la règle égale donne déjà des parts justes"],
        }
    return {
        "rule": Group.SplitRule.BY_WEIGHT.value,
        "label": Group.SplitRule.BY_WEIGHT.label,
        "reasons": [
            f"les poids vont de {_decimal_text(min(weights))} à {_decimal_text(max(weights))} "
            f"(le plus élevé vaut {_decimal_text(ratio.quantize(Decimal('0.1')))} fois le plus bas)",
            "avec la règle égale, les membres au poids le plus faible paieraient autant que les autres "
            "malgré des moyens plus réduits",
        ],
    }


def human_amount(amount) -> str:
    """50.000 → « 50 », 17.500 → « 17,5 » : « 50.000 » se lirait « cinquante mille » en français."""
    text = f"{Decimal(str(amount)).quantize(Decimal('0.001')):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def coach_template(comparison: dict, recommendation: dict, currency: str) -> str:
    lines = [f"**Recommandation : règle « {recommendation['label'].lower()} ».**", ""]
    lines += [f"- Parce que {reason}." for reason in recommendation["reasons"]]
    lines += ["", "| Membre | Poids | Égale | Par poids |", "|---|---|---|---|"]
    lines += [
        f"| {r['name']} | {r['weight']} | {human_amount(r['equal'])} {currency} | {human_amount(r['by_weight'])} {currency} |"
        for r in comparison["rows"]
    ]
    return "\n".join(lines)


def coach_answer(group, amount, members: list[dict], question: str = "", user=None) -> dict:
    """Compare les deux règles, recommande, puis fait expliquer la recommandation par le LLM
    (repli : explication par modèle de texte). Ne lève jamais d'erreur LLM."""
    import time

    from core.ai import LLMError, call_llm

    started = time.perf_counter()
    comparison = equity_comparison(amount, members)
    recommendation = recommend_rule(members)
    prompt = (
        "Tu es le coach d'équité de Finova (partage de dépenses). Explique simplement, en 120 mots maximum, "
        "la différence entre la règle égale et la règle par poids pour ce groupe, puis justifie la "
        "recommandation déjà calculée ci-dessous. Utilise uniquement ces chiffres, n'en invente aucun, ne parle "
        "jamais de JSON. Réponds dans la langue de la question si elle est fournie, sinon en français, en "
        "Markdown simple (gras, puces).\n"
        f"Montant à partager : {human_amount(comparison['amount'])} {group.currency}\n"
        "Répartition comparée (montants déjà écrits à la française, à reprendre tels quels) :\n"
        + "\n".join(
            f"- {r['name']} (poids {r['weight']}) : règle égale {human_amount(r['equal'])} {group.currency}, "
            f"par poids {human_amount(r['by_weight'])} {group.currency}"
            for r in comparison["rows"]
        )
        + "\n"
        f"Recommandation : {recommendation['label']} — raisons : {'; '.join(recommendation['reasons'])}\n"
        f"Question du membre : {question or '(aucune)'}"
    )
    try:
        text = (call_llm("m1.equity_coach", prompt, module="groups", user=user) or "").strip()
        if not text or len(text) > 2500:
            raise LLMError("Explication du coach invalide.")
        source = "llm"
    except LLMError:
        text = coach_template(comparison, recommendation, group.currency)
        source = "template"
    return {
        "comparison": comparison,
        "recommendation": {"rule": recommendation["rule"], "label": recommendation["label"]},
        "explanation": text,
        "source": source,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def parse_coach_members(raw) -> list[dict]:
    """Scénario envoyé par le navigateur, revalidé : 1 à 12 personnes, noms bornés,
    poids entre 0,001 et 100. Lève ValueError avec un message lisible."""
    import json

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        raise ValueError("Scénario illisible.")
    if not isinstance(data, list) or not 1 <= len(data) <= COACH_MAX_MEMBERS:
        raise ValueError(f"Il faut entre 1 et {COACH_MAX_MEMBERS} personnes.")
    members = []
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError("Scénario illisible.")
        name = str(item.get("name") or "").strip()[:40] or f"Personne {index}"
        try:
            weight = Decimal(str(item.get("weight"))).quantize(Decimal("0.001"))
        except Exception:
            raise ValueError(f"Poids invalide pour {name}.")
        if not Decimal("0.001") <= weight <= Decimal("100"):
            raise ValueError(f"Le poids de {name} doit être compris entre 0,001 et 100.")
        members.append({"name": name, "weight": weight})
    return members
