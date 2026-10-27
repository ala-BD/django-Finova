"""
Vérifie les seuils 80 % / 100 % de tous les budgets du mois en cours.

Filet de sécurité pour le planificateur (Celery beat ou cron Render) : les
alertes partent déjà à chaque dépense via le signal post_save, et les drapeaux
garantissent qu'une alerte n'est jamais envoyée deux fois.
"""

from django.core.management.base import BaseCommand

from budgets.models import Budget
from budgets.services import check_budget_alerts, current_month


class Command(BaseCommand):
    help = "Envoie les alertes de budget (80 % et 100 %) non encore envoyées pour le mois en cours."

    def handle(self, *args, **options):
        sent = 0
        budgets = Budget.objects.filter(month=current_month()).select_related("group", "category")
        for budget in budgets:
            sent += len(check_budget_alerts(budget))
        self.stdout.write(self.style.SUCCESS(f"{budgets.count()} budget(s) vérifié(s), {sent} alerte(s) envoyée(s)."))
