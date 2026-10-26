from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from budgets.models import Category
from groups.models import Group


class CategoryModelTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Colocation")
        self.other = Group.objects.create(name="Voyage")

    def test_name_unique_per_group_case_insensitive(self):
        Category.objects.create(group=self.group, name="Restaurant")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(group=self.group, name="restaurant")
        Category.objects.create(group=self.other, name="Restaurant")  # autre groupe : autorisé

    def test_color_format(self):
        category = Category(group=self.group, name="Transport", color="rouge")
        with self.assertRaises(ValidationError):
            category.full_clean()
        category.color = "#e11d48"
        category.full_clean()
