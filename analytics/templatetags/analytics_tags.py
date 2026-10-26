from django import template

from analytics.services import format_money, format_number

register = template.Library()


@register.filter
def money(value, currency="TND"):
    """{{ montant|money:"TND" }} → « 1 234,500 TND »."""
    if value in (None, ""):
        return ""
    return format_money(value, currency)


@register.filter
def number(value, places=1):
    if value in (None, ""):
        return ""
    return format_number(value, int(places))
