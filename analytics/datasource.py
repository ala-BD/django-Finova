"""
Source des dépenses pour M5.

M5 ne lit jamais les modèles des autres modules directement : il passe par le contrat
`expenses.services.get_group_expenses` (M2). Tant que M1 (groupes) et M2 (dépenses)
ne sont pas livrés, un jeu de démonstration généré est utilisé, et l'interface
l'indique clairement (ANALYTICS_DEMO_DATA, activé par défaut en développement).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.utils import timezone


@dataclass(frozen=True)
class ExpenseRecord:
    """Vue en lecture seule d'une dépense, indépendante du modèle M2."""

    id: int
    group_id: int
    group_name: str
    title: str
    amount: Decimal
    currency: str
    date: date
    category: str
    payer: str


@dataclass
class ExpenseSource:
    records: list[ExpenseRecord] = field(default_factory=list)
    is_demo: bool = False


def get_user_expenses(user) -> ExpenseSource:
    """Toutes les dépenses des groupes actifs de l'utilisateur."""
    try:
        return ExpenseSource(records=_load_real_expenses(user), is_demo=False)
    except (ImportError, LookupError, NotImplementedError):
        # M1 / M2 pas encore livrés : modèles absents ou contrat non implémenté.
        if not getattr(settings, "ANALYTICS_DEMO_DATA", False):
            return ExpenseSource(records=[], is_demo=False)
        return ExpenseSource(records=_demo_expenses_for(user), is_demo=True)


def _load_real_expenses(user) -> list[ExpenseRecord]:
    """Adaptateur vers M1/M2, écrit d'après le diagramme de classes (à valider après le gel)."""
    from expenses.services import get_group_expenses
    from groups.models import Group

    groups = Group.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        is_archived=False,
    ).distinct()
    records = []
    for group in groups:
        for expense in get_group_expenses(group):
            records.append(
                ExpenseRecord(
                    id=expense.pk,
                    group_id=group.pk,
                    group_name=group.name,
                    title=expense.title,
                    amount=Decimal(expense.amount),
                    currency=expense.currency or group.currency,
                    date=expense.expense_date,
                    category=expense.category.name if expense.category_id else "Autre",
                    payer=str(expense.payer.user),
                )
            )
    return records


def _demo_expenses_for(user) -> list[ExpenseRecord]:
    from analytics.demo import dashboard_dataset

    display_name = user.get_short_name() or user.username
    return dashboard_dataset(today=timezone.localdate(), user_name=display_name).records
