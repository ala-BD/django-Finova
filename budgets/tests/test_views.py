from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from budgets.models import Budget, Category
from budgets.services import current_month

from .factories import PASSWORD, add_member, make_budget, make_category, make_expense, make_group, make_user


class BudgetViewsTestCase(TestCase):
    def setUp(self):
        self.admin = make_user("admin")
        self.group = make_group(admin=self.admin)
        self.member = add_member(self.group, make_user("membre")).user
        self.outsider = make_user("intrus")
        self.category = make_category(self.group, "Restaurants")
        self.month = current_month()
        self.budget = make_budget(self.category, self.month, limit="300")

    def url(self, name, **kwargs):
        return reverse(f"budgets:{name}", kwargs={"gid": self.group.pk, **kwargs})

    def all_group_urls(self):
        return [
            self.url("list"),
            self.url("create"),
            self.url("update", pk=self.budget.pk),
            self.url("delete", pk=self.budget.pk),
            self.url("simulate"),
            self.url("recommend") + f"?category={self.category.pk}",
            self.url("categories"),
            self.url("category_create"),
            self.url("category_update", pk=self.category.pk),
            self.url("category_delete", pk=self.category.pk),
        ]


class AccessControlTests(BudgetViewsTestCase):
    def test_anonymous_redirected_to_login_with_next(self):
        for url in self.all_group_urls() + [reverse("budgets:overview")]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse("accounts:login") + "?next=", response["Location"])

    def test_outsider_gets_403_on_every_group_url(self):
        self.client.force_login(self.outsider)
        for url in self.all_group_urls():
            self.assertEqual(self.client.get(url).status_code, 403, url)
        response = self.client.post(self.url("delete", pk=self.budget.pk))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Budget.objects.filter(pk=self.budget.pk).exists())

    def test_member_can_read_but_not_manage(self):
        self.client.force_login(self.member)
        for name in ("list", "simulate", "categories"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 200, name)
        for url in (
            self.url("create"),
            self.url("update", pk=self.budget.pk),
            self.url("delete", pk=self.budget.pk),
            self.url("category_create"),
            self.url("category_delete", pk=self.category.pk),
        ):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_budget_of_another_group_is_404(self):
        other_group = make_group(admin=self.admin)
        foreign = make_budget(make_category(other_group), self.month)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("update", pk=foreign.pk)).status_code, 404)

    def test_unknown_group_is_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("budgets:list", kwargs={"gid": 9999})).status_code, 404)

    def test_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(self.url("delete", pk=self.budget.pk))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Budget.objects.filter(pk=self.budget.pk).exists())


class BudgetCrudTests(BudgetViewsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.food = make_category(self.group, "Alimentation")

    def test_list_shows_progress_with_text(self):
        make_expense(self.category, "250", self.month)
        response = self.client.get(self.url("list"))
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, "83,3")
        self.assertContains(response, "Attention : 80 % atteints")
        self.assertContains(response, "Prévision de fin de mois")

    def test_list_for_past_month_has_no_forecast(self):
        response = self.client.get(self.url("list") + "?month=2020-01")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aucun budget défini")
        self.assertNotContains(response, "Prévision de fin de mois")

    def test_create_budget(self):
        response = self.client.post(
            self.url("create"),
            {"category": self.food.pk, "month": self.month.strftime("%Y-%m"), "limit_amount": "600"},
        )
        self.assertEqual(response.status_code, 302)
        budget = Budget.objects.get(category=self.food)
        self.assertEqual(budget.month, self.month)
        self.assertEqual(budget.group, self.group)
        self.assertEqual(budget.limit_amount, Decimal("600"))

    def test_create_rejects_duplicate_and_non_positive_limit(self):
        response = self.client.post(
            self.url("create"),
            {"category": self.category.pk, "month": self.month.strftime("%Y-%m"), "limit_amount": "100"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Un budget existe déjà")
        response = self.client.post(
            self.url("create"),
            {"category": self.food.pk, "month": self.month.strftime("%Y-%m"), "limit_amount": "0"},
        )
        self.assertContains(response, "strictement positive")
        self.assertContains(response, 'aria-invalid="true"')
        self.assertFalse(Budget.objects.filter(category=self.food).exists())

    def test_cannot_use_category_of_another_group(self):
        foreign = make_category(make_group(admin=self.admin))
        response = self.client.post(
            self.url("create"),
            {"category": foreign.pk, "month": self.month.strftime("%Y-%m"), "limit_amount": "100"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Budget.objects.filter(category=foreign).exists())

    def test_update_budget(self):
        response = self.client.post(
            self.url("update", pk=self.budget.pk),
            {
                "category": self.category.pk,
                "month": self.month.strftime("%Y-%m"),
                "limit_amount": "450",
                "carry_over": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.limit_amount, Decimal("450"))
        self.assertTrue(self.budget.carry_over)

    def test_delete_requires_post_and_keeps_expenses(self):
        expense = make_expense(self.category, "20", self.month)
        self.assertEqual(self.client.get(self.url("delete", pk=self.budget.pk)).status_code, 200)
        self.assertTrue(Budget.objects.filter(pk=self.budget.pk).exists())
        response = self.client.post(self.url("delete", pk=self.budget.pk))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Budget.objects.filter(pk=self.budget.pk).exists())
        expense.refresh_from_db()

    def test_overview_lists_user_groups_only(self):
        hidden = make_group(name="Groupe secret")
        response = self.client.get(reverse("budgets:overview"))
        self.assertContains(response, self.group.name)
        self.assertNotContains(response, hidden.name)


class CategoryCrudTests(BudgetViewsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_create_category(self):
        response = self.client.post(
            self.url("category_create"), {"name": "  Santé  ", "color": "#0e7490", "icon": "💊"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Category.objects.filter(group=self.group, name="Santé").exists())

    def test_duplicate_name_rejected_ignoring_case(self):
        response = self.client.post(self.url("category_create"), {"name": "restaurants", "color": "#0e7490"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "porte déjà ce nom")

    def test_invalid_color_rejected(self):
        response = self.client.post(self.url("category_create"), {"name": "Santé", "color": "rouge"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Category.objects.filter(name="Santé").exists())

    def test_delete_refused_while_expenses_attached(self):
        make_expense(self.category, "20", self.month)
        response = self.client.post(self.url("category_delete", pk=self.category.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "choisissez une catégorie de réaffectation")
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_delete_with_reassignment_moves_expenses(self):
        target = make_category(self.group, "Divers")
        expense = make_expense(self.category, "20", self.month)
        response = self.client.post(self.url("category_delete", pk=self.category.pk), {"reassign_to": target.pk})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Category.objects.filter(pk=self.category.pk).exists())
        expense.refresh_from_db()
        self.assertEqual(expense.category, target)

    def test_delete_unused_category(self):
        unused = make_category(self.group, "Vide")
        response = self.client.post(self.url("category_delete", pk=unused.pk))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Category.objects.filter(pk=unused.pk).exists())


class SimulateAndRecommendViewTests(BudgetViewsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.member)
        for month in (date(2026, 7, 10), date(2026, 8, 10), date(2026, 9, 10)):
            make_expense(self.category, "300", month)

    @mock.patch("budgets.services.timezone.localdate", return_value=date(2026, 10, 5))
    def test_simulate_json(self, _):
        response = self.client.get(
            self.url("simulate"),
            {"category": self.category.pk, "reduction_percent": "20", "savings_goal": "360"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["monthly_saving"], "60.000")
        self.assertEqual(data["result"]["months_needed"], 6)
        self.assertEqual(data["result"]["reached_on"], "2027-04")

    def test_simulate_rejects_percent_above_100(self):
        response = self.client.get(
            self.url("simulate"),
            {"category": self.category.pk, "reduction_percent": "150", "savings_goal": "360", "format": "json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("reduction_percent", response.json()["errors"])

    @mock.patch("budgets.services.timezone.localdate", return_value=date(2026, 10, 5))
    def test_simulate_html_fallback_without_javascript(self, _):
        response = self.client.get(
            self.url("simulate"),
            {"category": self.category.pk, "reduction_percent": "20", "savings_goal": "360"},
        )
        self.assertContains(response, "6 mois")

    def test_recommend_json(self):
        with mock.patch("core.ai.call_llm", side_effect=Exception("API indisponible")):
            response = self.client.get(
                self.url("recommend"), {"category": self.category.pk, "month": "2026-10"}
            )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["suggested_limit"], "330.000")
        self.assertEqual(data["source"], "fallback")

    def test_recommend_requires_category_of_the_group(self):
        foreign = make_category(make_group())
        response = self.client.get(self.url("recommend"), {"category": foreign.pk})
        self.assertEqual(response.status_code, 400)
        response = self.client.get(self.url("recommend"))
        self.assertEqual(response.status_code, 400)


class LoginFlowTests(BudgetViewsTestCase):
    def test_real_login_then_access(self):
        self.client.post(reverse("accounts:login"), {"username": "membre", "password": PASSWORD})
        self.assertEqual(self.client.get(self.url("list")).status_code, 200)
