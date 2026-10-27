from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from budgets.models import Budget
from budgets.services import current_month
from expenses.models import Expense
from groups.models import Group

from .factories import make_budget, make_category, make_expense, make_group


class CheckBudgetAlertsCommandTests(TestCase):
    def test_sends_pending_alerts_once(self):
        category = make_category(make_group())
        budget = make_budget(category, current_month(), limit="100")
        with mock.patch("automation.services.notify", side_effect=NotImplementedError):
            make_expense(category, "95", current_month())  # notify indisponible : alerte en attente
        out = StringIO()
        with mock.patch("automation.services.notify") as notify:
            call_command("check_budget_alerts", stdout=out)
            call_command("check_budget_alerts", stdout=out)
        self.assertEqual(notify.call_count, 1)
        budget.refresh_from_db()
        self.assertTrue(budget.alert_80_sent)


class SeedDemoCommandTests(TestCase):
    def test_seed_is_reproducible_and_idempotent(self):
        with mock.patch("automation.services.notify"):
            call_command("seed_demo", stdout=StringIO())
            first_count = Expense.objects.count()
            call_command("seed_demo", stdout=StringIO())  # déjà présent : ne fait rien
            self.assertEqual(Expense.objects.count(), first_count)
            call_command("seed_demo", "--reset", stdout=StringIO())
        group = Group.objects.get(name__contains="démo")
        self.assertEqual(Expense.objects.count(), first_count)
        self.assertEqual(group.memberships.count(), 6)
        self.assertEqual(Budget.objects.filter(group=group, month=current_month()).count(), 6)
