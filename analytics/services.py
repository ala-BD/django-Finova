"""Logique métier M5 : filtres, agrégats du tableau de bord et résumé narratif du mois."""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from core.ai import call_llm

MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

FILTER_KEYS = ("start", "end", "group", "category", "member", "min_amount", "max_amount", "currency")


# --- Filtres -----------------------------------------------------------------

def params_to_json(cleaned: dict) -> dict:
    """Paramètres de filtre validés → dict sérialisable (SavedFilter.params, query string)."""
    params = {}
    for key in FILTER_KEYS:
        value = cleaned.get(key)
        if value in (None, ""):
            continue
        params[key] = value.isoformat() if isinstance(value, date) else str(value)
    return params


def apply_filters(records, params: dict):
    """`params` au format de params_to_json. Le montant est comparé dans la devise des dépenses."""
    start = _parse_date(params.get("start"))
    end = _parse_date(params.get("end"))
    group = str(params.get("group") or "")
    category = params.get("category") or ""
    member = params.get("member") or ""
    min_amount = _parse_decimal(params.get("min_amount"))
    max_amount = _parse_decimal(params.get("max_amount"))

    result = []
    for record in records:
        if start and record.date < start:
            continue
        if end and record.date > end:
            continue
        if group and str(record.group_id) != group:
            continue
        if category and record.category != category:
            continue
        if member and record.payer != member:
            continue
        if min_amount is not None and record.amount < min_amount:
            continue
        if max_amount is not None and record.amount > max_amount:
            continue
        result.append(record)
    return result


def describe_params(params: dict, group_names: dict | None = None) -> str:
    """Description lisible d'un filtre (liste, rapport PDF)."""
    parts = []
    if params.get("start") or params.get("end"):
        start = _format_date(params.get("start")) or "…"
        end = _format_date(params.get("end")) or "…"
        parts.append(f"du {start} au {end}")
    if params.get("group"):
        parts.append(f"groupe {(group_names or {}).get(str(params['group']), params['group'])}")
    if params.get("category"):
        parts.append(f"catégorie {params['category']}")
    if params.get("member"):
        parts.append(f"payé par {params['member']}")
    if params.get("min_amount"):
        parts.append(f"montant min. {params['min_amount']}")
    if params.get("max_amount"):
        parts.append(f"montant max. {params['max_amount']}")
    if params.get("currency"):
        parts.append(f"en {params['currency']}")
    return ", ".join(parts) or "toutes les dépenses"


# --- Agrégats ----------------------------------------------------------------

def total(records) -> Decimal:
    return sum((r.amount for r in records), Decimal("0"))


def totals_by(records, key) -> list[tuple[str, Decimal]]:
    sums = defaultdict(Decimal)
    for record in records:
        sums[key(record)] += record.amount
    return sorted(sums.items(), key=lambda item: item[1], reverse=True)


def totals_by_month(records) -> list[tuple[str, Decimal]]:
    sums = defaultdict(Decimal)
    for record in records:
        sums[month_key(record.date)] += record.amount
    return sorted(sums.items())


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_label(key: str) -> str:
    year, month = key.split("-")
    return f"{MONTHS_FR[int(month) - 1]} {year}"


def dashboard_data(records, categories_order) -> dict:
    """Agrégats et séries prêts pour Chart.js et pour les tableaux accessibles."""
    by_category = totals_by(records, lambda r: r.category)
    by_month = totals_by_month(records)
    by_member = totals_by(records, lambda r: r.payer)
    grand_total = total(records)
    count = len(records)
    color_slot = {name: index for index, name in enumerate(categories_order)}
    # Catégories propres à un groupe (« Loyer », « Courses »…) : emplacements restants, par ordre
    # alphabétique, pour qu'elles ne tombent pas toutes sur la même couleur.
    unknown = sorted({name for name, _ in by_category} - set(color_slot))
    used = {color_slot[name] for name, _ in by_category if name in color_slot}
    free = [slot for slot in range(len(categories_order)) if slot not in used] or list(range(len(categories_order)))
    for index, name in enumerate(unknown):
        color_slot[name] = free[index % len(free)]
    return {
        "total": grand_total,
        "count": count,
        "average": (grand_total / count).quantize(Decimal("0.001")) if count else Decimal("0"),
        "by_category": [
            {"label": name, "value": value, "share": _share(value, grand_total)} for name, value in by_category
        ],
        "by_month": [{"key": key, "label": month_label(key), "value": value} for key, value in by_month],
        "by_member": [{"label": name, "value": value} for name, value in by_member],
        "charts": {
            "category": {
                "labels": [name for name, _ in by_category],
                "values": [float(value) for _, value in by_category],
                "slots": [color_slot[name] for name, _ in by_category],
            },
            "month": {
                "labels": [month_label(key) for key, _ in by_month],
                "values": [float(value) for _, value in by_month],
            },
            "member": {
                "labels": [name for name, _ in by_member],
                "values": [float(value) for _, value in by_member],
            },
        },
    }


def latest_month(records) -> str | None:
    return max((month_key(r.date) for r in records), default=None)


def month_aggregates(records, month: str, currency: str, today: date | None = None) -> dict:
    """
    Agrégats d'un mois : seules données transmises au LLM (jamais les dépenses brutes).
    `today` signale un mois en cours, donc incomplet.
    """
    current = [r for r in records if month_key(r.date) == month]
    previous_key = _previous_month(month)
    previous = [r for r in records if month_key(r.date) == previous_key]
    current_total = total(current)
    previous_total = total(previous)
    variation = None
    if previous_total:
        variation = ((current_total - previous_total) / previous_total * 100).quantize(Decimal("0.1"))
    biggest = max(current, key=lambda r: r.amount, default=None)
    return {
        "month": month,
        "month_label": month_label(month),
        "as_of": today.isoformat() if today and month_key(today) == month else None,
        "previous_month_label": month_label(previous_key),
        "currency": currency,
        "count": len(current),
        "total": current_total.quantize(Decimal("0.001")),
        "previous_total": previous_total.quantize(Decimal("0.001")),
        "variation_pct": variation,
        "categories": [
            {"name": name, "total": value.quantize(Decimal("0.001")), "share_pct": _share(value, current_total)}
            for name, value in totals_by(current, lambda r: r.category)
        ],
        "biggest": (
            {
                "title": biggest.title,
                "amount": biggest.amount.quantize(Decimal("0.001")),
                "category": biggest.category,
                "date": biggest.date.isoformat(),
            }
            if biggest
            else None
        ),
    }


# --- Résumé narratif ---------------------------------------------------------

def template_summary(agg: dict) -> str:
    """Résumé de repli, généré sans IA à partir des mêmes agrégats."""
    currency = agg["currency"]
    if not agg["count"]:
        return f"Aucune dépense enregistrée en {agg['month_label']}."
    period = agg["month_label"]
    if agg.get("as_of"):
        period += f" (mois en cours, au {date.fromisoformat(agg['as_of']):%d/%m/%Y})"
    text = (
        f"En {period}, vous avez dépensé {format_money(agg['total'], currency)} "
        f"sur {agg['count']} dépense{'s' if agg['count'] > 1 else ''}"
    )
    variation = agg["variation_pct"]
    if variation is not None:
        trend = "une hausse" if variation > 0 else "une baisse" if variation < 0 else "une stabilité"
        text += (
            f", soit {trend} de {format_number(abs(variation), 1)} % par rapport à "
            f"{agg['previous_month_label']} ({format_money(agg['previous_total'], currency)})"
        )
    text += "."
    if agg["categories"]:
        top = agg["categories"][0]
        text += (
            f" Le premier poste est {top['name']} avec {format_money(top['total'], currency)} "
            f"({format_number(top['share_pct'], 1)} % du total)."
        )
    if agg["biggest"]:
        biggest = agg["biggest"]
        day = date.fromisoformat(biggest["date"])
        text += (
            f" La plus grosse dépense est « {biggest['title']} » "
            f"({format_money(biggest['amount'], currency)}, le {day:%d/%m/%Y})."
        )
    return text


PROMPT_TOP_CATEGORIES = 3


def llm_payload(agg: dict) -> dict:
    """Agrégats déjà formatés en français : le LLM les recopie sans calcul ni conversion."""
    currency = agg["currency"]

    def money(value):
        # « 900,000 TND » est lu « neuf cent mille » par le modèle : pas de décimales si le montant est rond.
        places = 0 if value == value.to_integral_value() else 3
        return f"{_plain(format_number(value, places))} {currency}"

    def percent(value):
        return f"{_plain(format_number(value, 1))} %"

    payload = {
        "mois": agg["month_label"],
        "nombre_de_depenses": agg["count"],
        "total_du_mois": money(agg["total"]),
        "mois_precedent": agg["previous_month_label"],
        "total_du_mois_precedent": money(agg["previous_total"]),
    }
    if agg.get("as_of"):
        payload["mois_en_cours"] = f"oui, chiffres arrêtés au {date.fromisoformat(agg['as_of']):%d/%m/%Y}"
    variation = agg["variation_pct"]
    if variation is not None:
        trend = "hausse" if variation > 0 else "baisse" if variation < 0 else "stabilité"
        payload["evolution"] = f"{trend} de {percent(abs(variation))}"
    payload["principales_categories"] = [
        {"categorie": c["name"], "montant": money(c["total"]), "part_du_total": percent(c["share_pct"])}
        for c in agg["categories"][:PROMPT_TOP_CATEGORIES]
    ]
    if agg["biggest"]:
        biggest = agg["biggest"]
        payload["plus_grosse_depense"] = {
            "libelle": biggest["title"],
            "montant": money(biggest["amount"]),
            "categorie": biggest["category"],
            "date": f"{date.fromisoformat(biggest['date']):%d/%m/%Y}",
        }
    return payload


def build_prompt(agg: dict) -> str:
    data = json.dumps(llm_payload(agg), ensure_ascii=False, indent=2)
    return (
        "Tu es l'assistant financier de l'application Finova.\n"
        "Rédige en français un résumé de 3 phrases des dépenses du mois, en vouvoyant l'utilisateur.\n"
        "Règles strictes :\n"
        "- recopie les nombres, montants, pourcentages et dates EXACTEMENT comme ils sont écrits "
        "dans les données (format français) ;\n"
        "- ne fais aucun calcul et n'invente aucun chiffre ;\n"
        "- ne cite que les catégories fournies ;\n"
        "- si « mois_en_cours » est présent, précise que le mois n'est pas terminé ;\n"
        "- texte simple : pas de liste, pas de titre, pas de mise en forme.\n"
        f"Données (JSON) :\n{data}"
    )


def generate_narrative_summary(agg: dict, user=None) -> dict:
    """
    Résumé par LLM, vérifié ; repli sur le modèle de texte en cas d'erreur ou d'écart.
    Renvoie {"text", "source": "llm" | "template", "reason", "latency_ms"}.
    """
    started = time.perf_counter()
    fallback = template_summary(agg)
    if not agg["count"]:
        return _summary(fallback, "template", "Aucune dépense ce mois-ci.", started)
    try:
        response = call_llm("monthly_summary", build_prompt(agg), module="analytics", user=user)
    except Exception as exc:  # l'IA n'est jamais bloquante
        return _summary(fallback, "template", f"IA indisponible : {exc}", started)

    text = " ".join((response.get("text", "") if isinstance(response, dict) else str(response or "")).split())
    if not text:
        return _summary(fallback, "template", "Réponse vide du modèle.", started)
    unknown = unverified_numbers(text, agg)
    if unknown:
        return _summary(
            fallback, "template", f"Chiffres non conformes aux agrégats : {', '.join(unknown)}.", started
        )
    return _summary(text, "llm", "", started)


NUMBER_RE = re.compile(r"\d(?:[\d   .,]*\d)?")


def unverified_numbers(text: str, agg: dict) -> list[str]:
    """Nombres du texte qui ne proviennent pas des agrégats fournis."""
    allowed = _allowed_numbers(agg)
    unknown = []
    for raw in NUMBER_RE.findall(text):
        value = _parse_number(raw)
        if value is None:
            continue
        if not any(abs(value - candidate) < Decimal("0.0005") for candidate in allowed):
            unknown.append(raw.strip())
    return unknown


def _allowed_numbers(agg: dict) -> set[Decimal]:
    values: set[Decimal] = set()

    def add(value):
        if value is None:
            return
        value = abs(Decimal(value))
        for places in range(0, 4):
            values.add(value.quantize(Decimal("1").scaleb(-places)))

    year, month = agg["month"].split("-")
    for value in (year, month, agg["count"], agg["total"], agg["previous_total"], agg["variation_pct"]):
        add(value)
    previous_year = agg["previous_month_label"].split()[-1]
    add(previous_year)
    add(len(agg["categories"]))
    for category in agg["categories"]:
        add(category["total"])
        add(category["share_pct"])
    days = []
    if agg["biggest"]:
        add(agg["biggest"]["amount"])
        days.append(agg["biggest"]["date"])
    if agg.get("as_of"):
        days.append(agg["as_of"])
    for iso in days:
        day = date.fromisoformat(iso)
        add(day.day)
        add(day.month)
        add(day.year)
    return values


def _parse_number(raw: str) -> Decimal | None:
    """Accepte « 1 234,500 » (fr) comme « 1,234.50 » (en)."""
    cleaned = re.sub(r"[\s  ]", "", raw).strip(".,")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        decimal_sep = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        cleaned = cleaned.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        cleaned = cleaned.replace(",", ".")
        if cleaned.count(".") > 1:
            cleaned = cleaned.replace(".", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _summary(text, source, reason, started) -> dict:
    return {
        "text": text,
        "source": source,
        "reason": reason,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


# --- Formatage ---------------------------------------------------------------

def format_number(value, places: int = 3) -> str:
    """1234.5 → « 1 234,500 » (espace fine insécable comme séparateur de milliers)."""
    formatted = f"{Decimal(value):,.{places}f}"
    return formatted.replace(",", " ").replace(".", ",")


def format_money(value, currency: str) -> str:
    return f"{format_number(value, 3)} {currency}"


def _plain(text: str) -> str:
    """Espace normale à la place de l'espace fine insécable (texte destiné au LLM)."""
    return text.replace(" ", " ")


def _share(value: Decimal, whole: Decimal) -> Decimal:
    if not whole:
        return Decimal("0")
    return (value / whole * 100).quantize(Decimal("0.1"))


def _previous_month(key: str) -> str:
    year, month = (int(part) for part in key.split("-"))
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _format_date(value):
    parsed = _parse_date(value)
    return f"{parsed:%d/%m/%Y}" if parsed else ""
