"""Contrat M2 : get_group_expenses. Yahya complète le reste du service (parts, tickets)."""

from .models import Expense


def get_group_expenses(group, period=None, category=None):
    """
    Source unique des dépenses filtrées, utilisée par M3, M4, M5 et M6.

    - period : tuple (début, fin) de dates incluses, ou None pour tout l'historique
    - category : instance de Category, ou None pour toutes les catégories
    Retourne un QuerySet, pour que l'appelant puisse agréger côté base (Sum, etc.).
    """
    expenses = Expense.objects.filter(group=group)
    if period is not None:
        start, end = period
        expenses = expenses.filter(expense_date__range=(start, end))
    if category is not None:
        expenses = expenses.filter(category=category)
    return expenses
