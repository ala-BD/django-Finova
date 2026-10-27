"""Petits constructeurs partagés par les tests M4."""

from datetime import date
from decimal import Decimal
from itertools import count

from django.contrib.auth import get_user_model

from budgets.models import Budget, Category
from expenses.models import Expense
from groups.models import Group, Membership

_sequence = count(1)
PASSWORD = "un-mot-de-passe-fort"


def make_user(username=None, **extra):
    username = username or f"user{next(_sequence)}"
    return get_user_model().objects.create_user(
        username=username, email=f"{username}@example.com", password=PASSWORD, **extra
    )


def make_group(admin=None, name=None, **extra):
    admin = admin or make_user()
    group = Group.objects.create(name=name or f"Groupe {next(_sequence)}", created_by=admin, **extra)
    Membership.objects.create(user=admin, group=group, role=Membership.Role.ADMIN)
    return group


def add_member(group, user=None, role=Membership.Role.MEMBER, **extra):
    user = user or make_user()
    return Membership.objects.create(user=user, group=group, role=role, **extra)


def make_category(group, name=None, **extra):
    return Category.objects.create(group=group, name=name or f"Catégorie {next(_sequence)}", **extra)


def make_budget(category, month, limit="300", **extra):
    return Budget.objects.create(
        group=category.group, category=category, month=month, limit_amount=Decimal(limit), **extra
    )


def make_expense(category, amount, day, title="Dépense", payer=None, group=None):
    group = group or category.group
    payer = payer or group.memberships.first()
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return Expense.objects.create(
        group=group,
        payer=payer,
        category=category,
        title=title,
        amount=Decimal(str(amount)),
        expense_date=day,
    )
