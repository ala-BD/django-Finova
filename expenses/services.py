"""Contrat M2 : get_group_expenses et utilitaires métiers (Yahya)."""

from django.db.models import QuerySet
from .models import Expense


def get_group_expenses(group, period=None, category=None) -> QuerySet[Expense]:
    """
    Source unique des dépenses filtrées d'un groupe.
    Exposée pour M3 (soldes), M4 (budgets), M5 (analytics) et M6 (automatisation).

    :param group: Instance ou identifiant du groupe
    :param period: Chaîne formatée 'YYYY-MM' ou objet date/datetime pour filtrer par mois
    :param category: Instance ou identifiant de catégorie
    :return: QuerySet de Expense
    """
    qs = Expense.objects.filter(group=group).select_related(
        "payer__user", "category", "group"
    ).prefetch_related("shares__member__user", "items__claims__member__user")

    if category is not None:
        qs = qs.filter(category=category)

    if period:
        if isinstance(period, str) and len(period) == 7 and "-" in period:
            try:
                year, month = map(int, period.split("-"))
                qs = qs.filter(expense_date__year=year, expense_date__month=month)
            except ValueError:
                pass
        elif hasattr(period, "year") and hasattr(period, "month"):
            qs = qs.filter(expense_date__year=period.year, expense_date__month=period.month)

    return qs.order_by("-expense_date", "-created_at")
