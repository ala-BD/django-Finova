"""Tests d'intégration entre modules (branche integration) : M1 ↔ M2 ↔ M3 ↔ M4 ↔ M6."""

from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Category
from expenses.models import Expense
from expenses.services import create_expense, get_group_expenses
from groups.models import Group, Membership
from settlements import services as settlement_services

User = get_user_model()


class IntegrationFixtures:
    def setUp(self):
        self.ala = User.objects.create_user("ala", "ala@example.com", first_name="Ala")
        self.yahya = User.objects.create_user("yahya", "yahya@example.com", first_name="Yahya")
        self.linda = User.objects.create_user("linda", "linda@example.com", first_name="Linda")
        self.outsider = User.objects.create_user("imem", "imem@example.com")
        self.group = Group.objects.create(name="Colocation", created_by=self.ala)
        self.m_ala = Membership.objects.create(group=self.group, user=self.ala, role=Membership.Role.ADMIN)
        self.m_yahya = Membership.objects.create(group=self.group, user=self.yahya, weight=D("2"))
        self.m_linda = Membership.objects.create(group=self.group, user=self.linda)
        self.food = Category.objects.create(group=self.group, name="Restaurant")


class MembershipIsAdminTests(IntegrationFixtures, TestCase):
    """M2 lit `membership.is_admin` comme un attribut : ce doit être un booléen, pas une méthode."""

    def test_property_values(self):
        self.assertIs(self.m_ala.is_admin, True)
        self.assertIs(self.m_yahya.is_admin, False)
        self.m_ala.is_active = False
        self.assertIs(self.m_ala.is_admin, False)


class CreateExpenseContractTests(IntegrationFixtures, TestCase):
    def test_creates_expense_with_shares_summing_to_amount(self):
        expense = create_expense(group_id=self.group.pk, created_by=self.yahya, title="Courses",
                                 amount=D("100"), category="restaurant")
        self.assertEqual((expense.payer, expense.category, expense.currency), (self.m_yahya, self.food, "TND"))
        self.assertEqual(sum(s.amount_due for s in expense.shares.all()), D("100"))
        self.assertEqual(expense.shares.count(), 3)

    def test_uses_group_default_split_rule(self):
        self.group.default_split_rule = Group.SplitRule.BY_WEIGHT
        self.group.save()
        expense = create_expense(group=self.group, created_by=self.ala, title="Loyer", amount=D("400"))
        shares = {s.member_id: s.amount_due for s in expense.shares.all()}
        self.assertEqual(shares[self.m_yahya.pk], D("200.000"))  # poids 2 sur 4

    def test_unknown_category_name_left_empty(self):
        expense = create_expense(group=self.group, created_by=self.ala, title="Divers", amount=D("5"),
                                 category="general")
        self.assertIsNone(expense.category)

    def test_validation(self):
        with self.assertRaises(ValueError):
            create_expense(group=self.group, created_by=self.outsider, title="X", amount=D("5"))
        with self.assertRaises(ValidationError):
            create_expense(group=self.group, created_by=self.ala, title="X", amount=D("0"))
        with self.assertRaises(ValidationError):
            create_expense(group=self.group, created_by=self.ala, title="X", amount=D("5"),
                           incurred_on=timezone.localdate() + timedelta(days=3))
        self.assertFalse(Expense.objects.exists())


class GroupExpensesFilterTests(IntegrationFixtures, TestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        create_expense(group=self.group, created_by=self.ala, title="Resto", amount=D("30"), category=self.food)
        create_expense(group=self.group, created_by=self.ala, title="Ancien", amount=D("10"),
                       incurred_on=today - timedelta(days=400))

    def test_category_by_name_id_or_instance(self):
        for value in ("Restaurant", "RESTAURANT", self.food.pk, str(self.food.pk), self.food):
            with self.subTest(value=value):
                self.assertEqual([e.title for e in get_group_expenses(self.group, category=value)], ["Resto"])

    def test_assistant_period_keywords(self):
        self.assertEqual(get_group_expenses(self.group, period="all").count(), 2)
        self.assertEqual(get_group_expenses(self.group, period="week").count(), 1)
        self.assertEqual(get_group_expenses(self.group, period="year").count(), 1)


class BalancesFromRealExpensesTests(IntegrationFixtures, TestCase):
    """M3 calcule les soldes à partir des vraies parts M2."""

    def test_balances_and_simplified_transfers(self):
        create_expense(group=self.group, created_by=self.ala, title="Courses", amount=D("90"))
        settlement = settlement_services.create_settlement(self.group, self.m_yahya, self.m_ala, D("30"))
        settlement_services.confirm_settlement(settlement, self.ala)

        balances = settlement_services.compute_balances(self.group)
        self.assertEqual(balances, {self.ala.pk: D("30"), self.yahya.pk: D("0"), self.linda.pk: D("-30")})
        self.assertEqual(settlement_services.simplify_debts(balances), [(self.linda.pk, self.ala.pk, D("30"))])
        self.assertTrue(settlement_services.balance_overview(self.group)["expenses_integrated"])


class RecurringExpenseGenerationTests(IntegrationFixtures, TestCase):
    """M6 génère une vraie dépense M2 via create_expense, puis notifie."""

    def test_generate_recurring_creates_expense_once(self):
        from automation.models import Notification, RecurringExpense
        from automation.services import generate_recurring_expenses

        today = timezone.localdate()
        RecurringExpense.objects.create(
            group_id=self.group.pk, owner=self.ala, title="Loyer", amount=D("600"),
            category="Restaurant", start_date=today, next_run=today,
        )
        self.assertEqual(generate_recurring_expenses(today), 1)
        self.assertEqual(generate_recurring_expenses(today), 0)  # idempotent
        expense = Expense.objects.get()
        self.assertEqual((expense.title, expense.payer, expense.category), ("Loyer", self.m_ala, self.food))
        self.assertEqual(sum(s.amount_due for s in expense.shares.all()), D("600"))
        self.assertTrue(Notification.objects.filter(user=self.ala, kind="recurring_expense_generated").exists())


class AssistantRoutingWithRealLLMOutputTests(IntegrationFixtures, TestCase):
    """M6 : Gemini répond ```json {...} ``` ; l'assistant doit l'accepter (bug vu en conditions réelles)."""

    def test_fenced_json_is_parsed(self):
        from automation.services import _parse_function_call

        raw = '```json\n{\n  "function": "get_balance",\n  "arguments": {}\n}\n```'
        self.assertEqual(_parse_function_call(raw), ("get_balance", {}))

    def test_unusable_routing_falls_back_to_keywords(self):
        from unittest.mock import patch

        from automation.services import _route_question

        for raw in ("je ne sais pas", '{"function": "execute_sql", "arguments": {}}'):
            with self.subTest(raw=raw), patch("automation.services.call_llm", return_value=raw):
                self.assertEqual(_route_question(self.ala, "combien je dois ?")[0], "get_balance")


class AssistantBalanceTests(IntegrationFixtures, TestCase):
    """M6 + M3 : « combien je dois ? » doit porter sur l'utilisateur qui pose la question."""

    def test_get_balance_returns_only_the_asking_user(self):
        from automation.services import _execute_assistant_function

        create_expense(group=self.group, created_by=self.ala, title="Courses", amount=D("90"))
        data = _execute_assistant_function(self.linda, "get_balance", {})
        self.assertEqual(data, [{
            "groupe": "Colocation", "devise": "TND", "solde": "-30.000", "situation": "débiteur",
            "je_dois": [{"à": "Ala", "montant": "30.000"}], "on_me_doit": [],
        }])


class StaticAssetsTests(TestCase):
    """Une accolade perdue lors d'une fusion désactive toutes les règles CSS qui la suivent."""

    def test_css_braces_are_balanced(self):
        import re
        from pathlib import Path

        from django.conf import settings

        for css in Path(settings.BASE_DIR, "static").rglob("*.css"):
            text = re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)
            text = re.sub(r"\"[^\"]*\"|'[^']*'", "", text)
            depth = 0
            for char in text:
                depth += {"{": 1, "}": -1}.get(char, 0)
                self.assertGreaterEqual(depth, 0, f"accolade fermante en trop dans {css.name}")
            self.assertEqual(depth, 0, f"accolade non fermée dans {css.name}")
            self.assertNotRegex(text, r"^(<<<<<<<|=======|>>>>>>>)", f"marqueur de conflit dans {css.name}")


class ExpenseUrlsTests(IntegrationFixtures, TestCase):
    def test_expenses_mounted_under_group_and_linked(self):
        self.assertEqual(reverse("expenses:list", kwargs={"gid": self.group.pk}), f"/groups/{self.group.pk}/expenses/")
        self.client.force_login(self.yahya)
        self.assertEqual(self.client.get(f"/groups/{self.group.pk}/expenses/").status_code, 200)
        detail = self.client.get(f"/groups/{self.group.pk}/")
        self.assertContains(detail, f'href="/groups/{self.group.pk}/expenses/"')

    def test_member_cannot_manage_others_expense(self):
        expense = create_expense(group=self.group, created_by=self.ala, title="Courses", amount=D("10"))
        self.client.force_login(self.linda)  # ni payeuse ni admin
        response = self.client.get(reverse("expenses:edit", kwargs={"gid": self.group.pk, "pk": expense.pk}))
        self.assertEqual(response.status_code, 403)
