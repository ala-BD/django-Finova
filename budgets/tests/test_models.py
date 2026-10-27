from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from budgets.models import Budget, Category

from .factories import make_budget, make_category, make_expense, make_group

OCTOBER = date(2026, 10, 1)


class CategoryModelTests(TestCase):
    def setUp(self):
        self.group = make_group()

    def test_name_unique_per_group_ignoring_case(self):
        make_category(self.group, "Restaurants")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(group=self.group, name="restaurants")

    def test_same_name_allowed_in_another_group(self):
        make_category(self.group, "Restaurants")
        other = make_category(make_group(), "Restaurants")
        self.assertEqual(other.name, "Restaurants")

    def test_deleting_category_keeps_its_expenses(self):
        # Expense.category est en SET_NULL dans le modèle de Yahya (M2) : la base ne bloque pas,
        # c'est la vue de suppression qui impose la réaffectation (voir test_views).
        category = make_category(self.group)
        expense = make_expense(category, "10", "2026-10-02")
        category.delete()
        expense.refresh_from_db()
        self.assertIsNone(expense.category)

    def test_str_includes_icon(self):
        self.assertEqual(str(make_category(self.group, "Loisirs", icon="🎬")), "🎬 Loisirs")


class BudgetModelTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.category = make_category(self.group)

    def test_one_budget_per_category_and_month(self):
        make_budget(self.category, OCTOBER)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_budget(self.category, OCTOBER, limit="500")

    def test_limit_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_budget(self.category, OCTOBER, limit="0")

    def test_month_must_be_first_day(self):
        budget = Budget(group=self.group, category=self.category, month=date(2026, 10, 15), limit_amount=10)
        with self.assertRaises(ValidationError):
            budget.full_clean()

    def test_category_must_belong_to_group(self):
        foreign = make_category(make_group())
        budget = Budget(group=self.group, category=foreign, month=OCTOBER, limit_amount=10)
        with self.assertRaises(ValidationError):
            budget.full_clean()

    def test_consumed_and_percent(self):
        budget = make_budget(self.category, OCTOBER, limit="200")
        make_expense(self.category, "50", "2026-10-03")
        self.assertEqual(budget.consumed(), 50)
        self.assertEqual(budget.percent_consumed(), 25)

    def test_deleting_budget_keeps_expenses(self):
        budget = make_budget(self.category, OCTOBER)
        expense = make_expense(self.category, "50", "2026-10-03")
        budget.delete()
        expense.refresh_from_db()
        self.assertEqual(expense.category, self.category)
