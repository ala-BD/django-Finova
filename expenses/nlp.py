"""M2 · IA (2) : saisir une dépense en langage naturel (Module M2 - Yahya).

« J'ai payé 45 dinars le resto hier avec Sami » → titre, montant, date, catégorie, participants.
Le LLM répond en JSON strict, validé ici (types, bornes, dates) ; s'il est indisponible ou
invalide, une analyse locale par règles prend le relais pour les champs qu'elle sait lire.
Le résultat ne fait que PRÉ-REMPLIR le formulaire : l'utilisateur vérifie avant d'enregistrer.

Les membres sont passés sous forme [(id, [noms possibles])] : pas d'accès à la base ici,
ce qui permet de mesurer l'extraction sur un jeu de phrases (commande m2_evaluate_nl).
"""

import json
import re
import time
from datetime import date, timedelta

from core.ai import LLMError, call_llm
from settlements.nlp import AMOUNT_WITH_CURRENCY, _extract_json, find_amount, find_receiver, normalize, to_amount

from .ml import LABELS, label_from_name, predict_category

MAX_PAST_DAYS = 366
DATE_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b")
VERBS = re.compile(
    r"^(?:j'?ai |on a |nous avons |il a |elle a )?(?:paye|regle|achete|depense|pris|mis|offert|avance)\b\s*"
)
NOISE = re.compile(
    r"\b(?:hier|avant-?hier|aujourd'?hui|ce (?:matin|soir|midi)|cette semaine|pour|le|la|les|l'|un|une|des|du|de|d')\b"
)


def local_date(text: str, today: date):
    """« hier », « avant-hier », « aujourd'hui », « 12/10 » ; None si rien de lisible."""
    if "avant-hier" in text or "avant hier" in text:
        return today - timedelta(days=2)
    if "hier" in text:
        return today - timedelta(days=1)
    if "aujourd" in text or "ce matin" in text or "ce soir" in text or "ce midi" in text:
        return today
    match = DATE_DMY.search(text)
    if match:
        day, month, year = match.groups()
        year = int(year) if year else today.year
        year = year + 2000 if year < 100 else year
        try:
            return date(year, int(month), int(day))
        except ValueError:
            return None
    return None


def local_title(text: str) -> str:
    """Phrase nettoyée : sans verbe, montant, date ni participants → « resto »."""
    text = re.sub(r"\bavec\b.*$", "", text)
    text = AMOUNT_WITH_CURRENCY.sub(" ", text)
    text = DATE_DMY.sub(" ", text)
    text = re.sub(r"\d+(?:[.,]\d+)?", " ", text)
    text = VERBS.sub("", text.strip())
    text = NOISE.sub(" ", text)
    words = [w for w in re.split(r"[\s,;.!?]+", text) if w]
    return " ".join(words)[:128].capitalize()


def parse_participants(raw_names, members) -> tuple[list[int], list[str]]:
    """Noms → ids des membres reconnus + noms inconnus (affichés, jamais inventés)."""
    ids, unknown = [], []
    for raw in raw_names[:12]:
        name = re.sub(r"\s+", " ", str(raw)).strip()[:40]
        if not name:
            continue
        member_id = find_receiver(normalize(name), members)
        if member_id is None:
            unknown.append(name)
        elif member_id not in ids:
            ids.append(member_id)
    return ids, unknown


def clean_date(raw, today: date):
    """Date ISO du LLM, refusée si future ou plus vieille d'un an."""
    try:
        value = date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None
    if value > today or (today - value).days > MAX_PAST_DAYS:
        return None
    return value


def parse_rule_based(text: str, members, today: date) -> dict:
    normalized = normalize(text)
    names = re.findall(r"\bavec ([a-z]+(?:(?: et |, ?)[a-z]+)*)", normalized)
    raw_names = re.split(r" et |, ?", names[0]) if names else []
    title = local_title(normalized)
    ids, unknown = parse_participants(raw_names, members)
    found = local_date(normalized, today)
    return {
        "title": title,
        "amount": find_amount(normalized),
        "expense_date": found if found and clean_date(found.isoformat(), today) else None,
        "category": predict_category(title or normalized)[0] if (title or normalized) else None,
        "participants": ids,
        "unknown_participants": unknown,
    }


def parse_with_llm(text: str, members, today: date, user=None) -> dict:
    """Lève LLMError ou ValueError si la réponse est inutilisable."""
    roster = "\n".join(f"- id {member_id} : {', '.join(names)}" for member_id, names in members)
    prompt = (
        "Extrais une dépense d'une phrase en français. Réponds UNIQUEMENT avec un objet JSON :\n"
        '{"title": "<objet de la dépense en 1 à 5 mots, sans montant ni date>", '
        '"amount": <nombre, montant total payé>, "date": "<AAAA-MM-JJ>", '
        f'"category": <une valeur parmi {", ".join(LABELS)}>, '
        '"participants": [<prénoms cités avec qui la dépense est partagée>]}\n'
        f"Aujourd'hui : {today.isoformat()} (« hier » = {(today - timedelta(days=1)).isoformat()}). "
        "Si la date n'est pas précisée, utilise celle d'aujourd'hui. "
        "Montants tunisiens : « 47.300 DT » = 47,3 dinars.\n"
        f"Membres du groupe :\n{roster}\nPhrase : {text}"
    )
    data = _extract_json(call_llm("m2.parse_expense", prompt, module="expenses", user=user))
    title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()[:128]
    category = str(data.get("category") or "").strip().lower()
    category = category if category in LABELS else label_from_name(category)
    participants = data.get("participants") or []
    if not isinstance(participants, list):
        raise ValueError("participants doit être une liste")
    ids, unknown = parse_participants(participants, members)
    return {
        "title": title[:1].upper() + title[1:],
        "amount": to_amount(data.get("amount")) if data.get("amount") not in (None, "") else None,
        "expense_date": clean_date(data.get("date"), today),
        "category": category,
        "participants": ids,
        "unknown_participants": unknown,
    }


def parse_expense(text: str, members, user=None, use_llm=True, today=None) -> dict:
    """Résultat validé + source ('llm', 'llm+regles' ou 'regles') + champs manquants + latence."""
    text = (text or "").replace("’", "'").strip()[:500]
    today = today or date.today()
    started = time.perf_counter()
    rules = parse_rule_based(text, members, today)
    result, source = rules, "regles"
    if use_llm:
        try:
            llm = parse_with_llm(text, members, today, user=user)
            merged = {key: llm[key] if llm[key] not in (None, "", []) else rules[key] for key in rules}
            # Même règle que M3 : un montant écrit à côté d'une devise est lu sans ambiguïté par
            # les règles (« 47.300 DT » = 47,3), le LLM le lit parfois 47 300.
            if rules["amount"] is not None and AMOUNT_WITH_CURRENCY.search(normalize(text)):
                merged["amount"] = rules["amount"]
            source = "llm" if merged == llm else "llm+regles"
            result = merged
        except (LLMError, ValueError, json.JSONDecodeError):
            pass
    missing = [label for key, label in (("title", "titre"), ("amount", "montant")) if not result[key]]
    return {
        **result,
        "source": source,
        "missing": missing,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
