from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(value, currency=""):
    """1234.5 → « 1 234,500 TND » (millimes, séparateurs français)."""
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    text = f"{amount:,.3f}".replace(",", " ").replace(".", ",")
    return f"{text} {currency}".strip()


@register.filter
def percent(value):
    """81.7 → « 81,7 % »."""
    if value is None:
        return "—"
    return f"{Decimal(value):.1f}".replace(".", ",") + " %"


@register.filter
def negate(value):
    return -Decimal(value) if value not in (None, "") else value


@register.filter
def bar_width(value):
    """Largeur CSS de la barre de progression, plafonnée à 100 %."""
    if value is None:
        return "0"
    return f"{min(Decimal(value), Decimal(100)):.1f}"


LEVEL_LABELS = {
    "ok": "Dans les clous",
    "warning": "Attention : 80 % atteints",
    "over": "Budget dépassé",
    "none": "Pas de budget",
}


@register.filter
def level_label(level):
    return LEVEL_LABELS.get(level, "")
