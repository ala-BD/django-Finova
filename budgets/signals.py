"""Alertes de budget déclenchées à chaque dépense enregistrée, sans couplage avec M2."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from expenses.models import Expense

from .services import check_alerts_for_expense


@receiver(post_save, sender=Expense, dispatch_uid="budgets_check_alerts_on_expense")
def check_budget_alerts_on_expense(sender, instance, **kwargs):
    check_alerts_for_expense(instance)
