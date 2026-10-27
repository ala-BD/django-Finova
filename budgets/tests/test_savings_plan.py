"""Plan d'économie : algorithme de répartition, service et vues."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from budgets import services
from budgets.models import Budget
from budgets.planning import flexibility_label, plan_savings, variation_of

from .factories import add_member, make_budget, make_category, make_expense, make_group, make_user

HISTORY = {
    "Logement": [1200] * 6,
    "Restaurants": [250, 320, 180, 300, 260, 340],
    "Loisirs": [90, 150, 60, 120, 40, 110],
}


class PlannerTests(SimpleTestCase):
    def test_monthly_target_is_fully_allocated_with_exact_sum(self):
        plan = plan_savings(HISTORY, 150, 3)
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.monthly_target, Decimal("50.000"))
        self.assertEqual(plan.monthly_saving, Decimal("50.000"))

    def test_fixed_expense_is_never_reduced(self):
        plan = plan_savings(HISTORY, 150, 3)
        rent = next(c for c in plan.categories if c.name == "Logement")
        self.assertEqual(rent.flexibility, "fixe")
        self.assertEqual(rent.reduction, 0)
        self.assertEqual(rent.new_target, Decimal("1200.000"))

    def test_irregular_categories_carry_the_effort(self):
        plan = plan_savings(HISTORY, 150, 3)
        cuts = {c.name: c.percent for c in plan.categories}
        self.assertGreater(cuts["Loisirs"], cuts["Restaurants"])  # plus irrégulier → plus gros effort relatif

    def test_cap_of_30_percent_and_redistribution(self):
        plan = plan_savings(HISTORY, 3000, 12)  # 250 / mois : au-delà des marges
        for category in plan.categories:
            self.assertLessEqual(category.percent, Decimal("30.0"))
            self.assertLessEqual(category.reduction, category.max_reduction)

    def test_unrealistic_goal_gives_months_needed(self):
        plan = plan_savings(HISTORY, 900, 2)
        self.assertFalse(plan.feasible)
        self.assertEqual(plan.monthly_saving, plan.max_monthly_saving)
        self.assertGreater(plan.months_needed, 2)

    def test_no_history(self):
        plan = plan_savings({"Vide": [], "Zéro": [0, 0]}, 100, 2)
        self.assertEqual(plan.categories, [])
        self.assertFalse(plan.feasible)
        self.assertIsNone(plan.months_needed)

    def test_variation_and_labels(self):
        self.assertEqual(variation_of([100, 100]), 0)
        self.assertEqual(variation_of([100]), 0.20)  # une seule valeur : variation inconnue
        self.assertEqual(flexibility_label(0.01), "fixe")
        self.assertEqual(flexibility_label(0.10), "peu flexible")
        self.assertEqual(flexibility_label(0.40), "flexible")


class SavingsPlanServiceTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.rent = make_category(self.group, "Logement")
        self.food = make_category(self.group, "Restaurants")
        for month, amount in ((5, "200"), (6, "320"), (7, "260"), (8, "180"), (9, "340")):
            make_expense(self.rent, "1000", date(2026, month, 1))
            make_expense(self.food, amount, date(2026, month, 10))

    def test_plan_uses_group_history(self):
        plan, categories = services.savings_plan(self.group, 120, 3, today=date(2026, 10, 15))
        self.assertEqual(set(categories), {"Logement", "Restaurants"})
        self.assertTrue(plan.feasible)
        food = next(c for c in plan.categories if c.name == "Restaurants")
        self.assertEqual(food.reduction, Decimal("40.000"))

    def test_apply_creates_then_updates_budgets(self):
        plan, categories = services.savings_plan(self.group, 120, 3, today=date(2026, 10, 15))
        november = date(2026, 11, 1)
        make_budget(self.rent, november, limit="900")
        created, updated = services.apply_savings_plan(self.group, plan, categories, november)
        self.assertEqual((created, updated), (1, 1))
        self.assertEqual(Budget.objects.get(category=self.food, month=november).limit_amount, Decimal("220.000"))
        self.assertEqual(Budget.objects.get(category=self.rent, month=november).limit_amount, Decimal("1000.000"))


@mock.patch("budgets.services.timezone.localdate", return_value=date(2026, 10, 15))
class SavingsPlanViewTests(TestCase):
    def setUp(self):
        self.admin = make_user()
        self.group = make_group(admin=self.admin)
        self.member = add_member(self.group).user
        food = make_category(self.group, "Restaurants")
        for month, amount in ((7, "200"), (8, "320"), (9, "260")):
            make_expense(food, amount, date(2026, month, 10))
        self.url = reverse("budgets:plan", kwargs={"gid": self.group.pk})
        self.apply_url = reverse("budgets:plan_apply", kwargs={"gid": self.group.pk})

    def test_member_sees_plan_without_apply_button(self, _):
        self.client.force_login(self.member)
        response = self.client.get(self.url, {"goal": "90", "months": "3"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Répartition par catégorie")
        self.assertContains(response, "30,000 TND / mois")
        self.assertNotContains(response, "Appliquer ce plan")

    def test_invalid_form(self, _):
        self.client.force_login(self.member)
        response = self.client.get(self.url, {"goal": "-5", "months": "40"})
        self.assertContains(response, 'aria-invalid="true"')
        self.assertNotContains(response, "Répartition par catégorie")

    def test_admin_applies_plan_to_next_month(self, _):
        self.client.force_login(self.admin)
        page = self.client.get(self.url, {"goal": "90", "months": "3"})
        self.assertContains(page, "Appliquer ce plan aux budgets de novembre 2026")
        response = self.client.post(self.apply_url, {"goal": "90", "months": "3"})
        self.assertRedirects(response, reverse("budgets:list", kwargs={"gid": self.group.pk}) + "?month=2026-11")
        self.assertEqual(Budget.objects.get(month=date(2026, 11, 1)).limit_amount, Decimal("230.000"))

    def test_member_and_outsider_cannot_apply(self, _):
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self.apply_url, {"goal": "90", "months": "3"}).status_code, 403)
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertFalse(Budget.objects.exists())
