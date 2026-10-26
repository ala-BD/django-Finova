"""
Conversion multi-devises.

Ordre de repli : cache Django (24 h) → API de taux (EXCHANGE_RATE_API_URL) →
derniers taux connus en base (ExchangeRate) → taux fixes documentés ci-dessous.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache

from analytics.models import ExchangeRate

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES = ("TND", "EUR", "USD")
CACHE_TTL_SECONDS = 24 * 60 * 60

# Taux fixes indicatifs (valeur d'1 unité en TND), relevés en octobre 2026.
# Utilisés uniquement si l'API et la base sont indisponibles.
FIXED_RATES_TO_TND = {
    "TND": Decimal("1"),
    "EUR": Decimal("3.400"),
    "USD": Decimal("3.100"),
}

MILLIME = Decimal("0.001")


@dataclass(frozen=True)
class RateQuote:
    rate: Decimal
    source: str  # identity, cache, api, db, fixed


def get_rate(base: str, target: str) -> RateQuote:
    base, target = base.upper(), target.upper()
    if base == target:
        return RateQuote(Decimal("1"), "identity")

    key = _cache_key(base, target)
    cached = cache.get(key)
    if cached is not None:
        return RateQuote(Decimal(cached), "cache")

    rate = _fetch_from_api(base, target)
    if rate is not None:
        return RateQuote(rate, "api")

    stored = ExchangeRate.objects.filter(base=base, target=target).first()
    if stored is not None:
        cache.set(key, str(stored.rate), CACHE_TTL_SECONDS)
        return RateQuote(stored.rate, "db")

    if base not in FIXED_RATES_TO_TND or target not in FIXED_RATES_TO_TND:
        raise ValueError(f"Devise non prise en charge : {base} → {target}")
    rate = (FIXED_RATES_TO_TND[base] / FIXED_RATES_TO_TND[target]).quantize(Decimal("0.000001"))
    return RateQuote(rate, "fixed")


def convert(amount, base: str, target: str) -> Decimal:
    return (Decimal(amount) * get_rate(base, target).rate).quantize(MILLIME)


def convert_records(records, target: str):
    """Renvoie (dépenses converties dans `target`, sources des taux utilisés)."""
    rates: dict[str, RateQuote] = {}
    converted = []
    for record in records:
        if record.currency not in rates:
            rates[record.currency] = get_rate(record.currency, target)
        quote = rates[record.currency]
        if quote.source == "identity":
            converted.append(record)
        else:
            converted.append(
                replace(record, amount=(record.amount * quote.rate).quantize(MILLIME), currency=target)
            )
    return converted, {currency: quote for currency, quote in rates.items() if quote.source != "identity"}


def _cache_key(base: str, target: str) -> str:
    return f"analytics:fx:{base}:{target}"


def _fetch_from_api(base: str, target: str) -> Decimal | None:
    """API au format open.er-api.com : {"result": "success", "rates": {"EUR": 0.29, ...}}."""
    url_template = getattr(settings, "EXCHANGE_RATE_API_URL", "")
    if not url_template:
        return None
    url = url_template.format(base=base)
    timeout = getattr(settings, "EXCHANGE_RATE_API_TIMEOUT", 3)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
        rates = payload["rates"]
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        logger.warning("API de taux indisponible (%s) : %s", url, exc)
        return None

    wanted = None
    for code in SUPPORTED_CURRENCIES:
        if code == base or code not in rates:
            continue
        try:
            rate = Decimal(str(rates[code])).quantize(Decimal("0.000001"))
        except InvalidOperation:
            continue
        ExchangeRate.objects.update_or_create(base=base, target=code, defaults={"rate": rate})
        cache.set(_cache_key(base, code), str(rate), CACHE_TTL_SECONDS)
        if code == target:
            wanted = rate
    return wanted
