"""M3 · IA (3) : déclarer un remboursement en langage naturel.

« J'ai remboursé 30 dinars à Ala hier en espèces » → bénéficiaire, montant, note.
Le LLM renvoie un JSON validé par Django ; s'il est indisponible ou invalide, une analyse
locale par règles (montant, prénom approché, mode de paiement) prend le relais.
Le résultat ne fait que PRÉ-REMPLIR le formulaire : l'utilisateur vérifie avant d'enregistrer.

Les membres sont passés sous forme [(id, [noms possibles])] : pas d'accès à la base ici,
ce qui permet de mesurer l'analyse sur un jeu de phrases (commande m3_evaluate_nl).
"""

import difflib
import json
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation

from core.ai import LLMError, call_llm

MAX_AMOUNT = Decimal("1000000")
MILLIME = Decimal("0.001")

AMOUNT_WITH_CURRENCY = re.compile(
    r"(\d+(?:[.,]\d{1,3})?)\s*(?:(?:dinars?|dt|tnd|d|euros?|eur|dollars?|usd|dh|mad)\b|€|\$)"
)
NUMBER = re.compile(r"(?<![\d/])(\d+(?:[.,]\d{1,3})?)(?![\d/])")
PAYMENT_MODES = [
    (r"\b(especes?|cash|liquide)\b", "Espèces"),
    (r"\bvirement\b", "Virement"),
    (r"\bcheque\b", "Chèque"),
    (r"\b(d17|flouci|e-?dinar|paiement mobile)\b", "Paiement mobile"),
    (r"\bcarte\b", "Carte bancaire"),
]
MODE_LABELS = [label for _, label in PAYMENT_MODES]
PURPOSE = re.compile(r"\bpour (?:le |la |les |l'|l |mon |ma |mes )?([a-z' -]{3,30}?)(?=$|[.,;!]| (?:en|par|hier|aujourd|ce|cette|a|au)\b)")
STOPWORDS = {"pour", "avec", "hier", "dinars", "dinar", "rembourse", "remboursement", "paye", "verse",
             "donne", "envoye", "especes", "virement", "cheque", "cash", "aujourd", "matin", "soir"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def to_amount(raw) -> Decimal | None:
    try:
        amount = Decimal(str(raw).replace(",", ".").replace(" ", "")).quantize(MILLIME)
    except (InvalidOperation, ValueError):
        return None
    return amount if Decimal("0") < amount <= MAX_AMOUNT else None


def find_amount(text: str) -> Decimal | None:
    """Montant suivi d'une devise en priorité, sinon premier nombre hors date (12/10)."""
    match = AMOUNT_WITH_CURRENCY.search(text)
    if match:
        return to_amount(match.group(1))
    for candidate in NUMBER.findall(text):
        amount = to_amount(candidate)
        if amount is not None:
            return amount
    return None


def find_receiver(text: str, members) -> int | None:
    """Nom exact d'abord, puis nom approché (fautes de frappe) avec difflib."""
    tokens = [t for t in re.findall(r"[a-z]+", text) if len(t) >= 3 and t not in STOPWORDS]
    index = {}
    for member_id, names in members:
        for name in names:
            for part in normalize(name).split():
                if len(part) >= 3:
                    index.setdefault(part, set()).add(member_id)
    for token in tokens:
        if len(index.get(token, ())) == 1:
            return next(iter(index[token]))
    for token in tokens:
        close = difflib.get_close_matches(token, list(index), n=1, cutoff=0.8)
        if close and len(index[close[0]]) == 1:
            return next(iter(index[close[0]]))
    return None


def find_note(text: str) -> str:
    parts = [label for pattern, label in PAYMENT_MODES if re.search(pattern, text)]
    purpose = PURPOSE.search(text)
    if purpose:
        parts.insert(0, f"Pour {purpose.group(1).strip()}")
    return " · ".join(dict.fromkeys(parts))[:200]


def parse_rule_based(text: str, members) -> dict:
    normalized = normalize(text)
    return {
        "receiver": find_receiver(normalized, members),
        "amount": find_amount(normalized),
        "note": find_note(normalized),
    }


def _extract_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("pas de JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("JSON inattendu")
    return data


def parse_with_llm(text: str, members, user=None) -> dict:
    """Lève LLMError ou ValueError si la réponse est inutilisable."""
    roster = "\n".join(f"- id {member_id} : {', '.join(names)}" for member_id, names in members)
    modes = ", ".join(f'"{label}"' for label in MODE_LABELS)
    prompt = (
        "Extrais un remboursement d'une phrase en français. Réponds UNIQUEMENT avec un objet JSON "
        '{"receiver_id": <id de la liste ou null>, "amount": <nombre ou null>, '
        f'"payment_mode": <une valeur parmi {modes}, ou "" si non précisé>, '
        '"purpose": "<objet du remboursement en 1 à 3 mots, sinon vide>"}.\n'
        "Exemples de modes : cash/liquide = Espèces, D17/Flouci/e-dinar = Paiement mobile.\n"
        f"Membres possibles (le bénéficiaire) :\n{roster}\nPhrase : {text}"
    )
    data = _extract_json(call_llm("m3.parse_repayment", prompt, module="settlements", user=user))
    valid_ids = {member_id for member_id, _ in members}
    receiver = data.get("receiver_id")
    if receiver is not None:
        try:
            receiver = int(receiver)
        except (TypeError, ValueError):
            receiver = None
    # Mode validé contre la liste fermée (même libellé que l'analyse locale) ; objet libre, borné
    mode = data.get("payment_mode") if data.get("payment_mode") in MODE_LABELS else ""
    purpose = re.sub(r"\s+", " ", str(data.get("purpose") or "")).strip()[:60]
    note = " · ".join(part for part in (f"Pour {purpose}" if purpose else "", mode) if part)
    return {
        "receiver": receiver if receiver in valid_ids else None,
        "amount": to_amount(data.get("amount")) if data.get("amount") is not None else None,
        "note": note[:200],
    }


def parse_repayment(text: str, members, user=None, use_llm=True) -> dict:
    """Résultat validé + source ('llm', 'llm+regles' ou 'regles') + latence.
    Les champs que le LLM n'a pas trouvés sont complétés par l'analyse locale."""
    text = (text or "").strip()[:500]
    started = time.perf_counter()
    rules = parse_rule_based(text, members)
    result, source = rules, "regles"
    if use_llm:
        try:
            llm = parse_with_llm(text, members, user=user)
            merged = {key: llm[key] if llm[key] not in (None, "") else rules[key] for key in rules}
            source = "llm" if merged == llm else "llm+regles"
            result = merged
        except (LLMError, ValueError, json.JSONDecodeError):
            pass
    missing = [label for key, label in (("receiver", "bénéficiaire"), ("amount", "montant")) if result[key] is None]
    return {
        **result,
        "source": source,
        "missing": missing,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
