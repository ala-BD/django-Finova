"""Contrat M2 : get_group_expenses, create_expense et utilitaires métiers (Yahya)."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from .models import Expense

# Règle de partage du groupe (M1) → règle de la dépense (M2)
GROUP_TO_EXPENSE_RULE = {
    "equal": Expense.SplitRule.EQUAL,
    "by_weight": Expense.SplitRule.BY_WEIGHT,
    "by_consumption": Expense.SplitRule.BY_ITEM,
}


def get_group_expenses(group, period=None, category=None) -> QuerySet[Expense]:
    """
    Source unique des dépenses filtrées d'un groupe.
    Exposée pour M3 (soldes), M4 (budgets), M5 (analytics) et M6 (automatisation).

    :param group: Instance ou identifiant du groupe
    :param period: 'YYYY-MM', objet date/datetime (mois), ou mot-clé de l'assistant M6 :
                   'week' (7 derniers jours), 'month' (mois en cours), 'year', 'all'
    :param category: Instance, identifiant, ou nom de catégorie (insensible à la casse)
    :return: QuerySet de Expense
    """
    qs = Expense.objects.filter(group=group).select_related(
        "payer__user", "category", "group"
    ).prefetch_related("shares__member__user", "items__claims__member__user")

    if category is not None:
        if isinstance(category, str) and not category.isdigit():
            qs = qs.filter(category__name__iexact=category.strip())
        else:
            qs = qs.filter(category=category)

    if period:
        today = timezone.localdate()
        if period == "week":
            qs = qs.filter(expense_date__gte=today - timedelta(days=7))
        elif period == "month":
            qs = qs.filter(expense_date__year=today.year, expense_date__month=today.month)
        elif period == "year":
            qs = qs.filter(expense_date__year=today.year)
        elif period == "all":
            pass
        elif isinstance(period, str) and len(period) == 7 and "-" in period:
            try:
                year, month = map(int, period.split("-"))
                qs = qs.filter(expense_date__year=year, expense_date__month=month)
            except ValueError:
                pass
        elif hasattr(period, "year") and hasattr(period, "month"):
            qs = qs.filter(expense_date__year=period.year, expense_date__month=period.month)

    return qs.order_by("-expense_date", "-created_at")


@transaction.atomic
def create_expense(
    *,
    group_id=None,
    group=None,
    created_by=None,
    payer=None,
    title,
    amount,
    category=None,
    incurred_on=None,
    description="",
    split_rule=None,
    recurring_expense_id=None,
) -> Expense:
    """
    Crée une dépense validée et calcule les parts (contrat utilisé par M6 pour les
    dépenses récurrentes).

    - Le payeur est l'appartenance active de `created_by` dans le groupe (ou `payer`).
    - `category` : instance, identifiant ou nom ; un nom inconnu laisse la dépense sans catégorie.
    - La règle de partage par défaut est celle du groupe.
    - `description` et `recurring_expense_id` sont acceptés pour le contrat M6 ; le modèle
      n'a pas de champ dédié, le lien est conservé côté M6 (RecurringOccurrence).
    Lève ValidationError si les données sont invalides, ValueError si le payeur n'est pas membre.
    """
    from groups.models import Group

    group = group or Group.objects.get(pk=group_id)
    if payer is None:
        payer = group.memberships.filter(user=created_by, is_active=True).first()
        if payer is None:
            raise ValueError("Le créateur de la dépense n'est pas membre actif du groupe.")

    expense = Expense(
        group=group,
        payer=payer,
        category=_resolve_category(group, category),
        title=str(title)[:128],
        amount=Decimal(str(amount)),
        currency=group.currency,
        expense_date=incurred_on or timezone.localdate(),
        split_rule=split_rule or GROUP_TO_EXPENSE_RULE.get(group.default_split_rule, Expense.SplitRule.EQUAL),
    )
    expense.full_clean()
    expense.save()
    expense.compute_shares()
    return expense


def predict_category(text, group=None) -> dict:
    """
    IA M2 : catégorie suggérée pour un titre de dépense (TF-IDF + régression logistique,
    repli par mots-clés si le modèle est absent). Ne lève jamais d'exception.

    :return: {label, confidence, confident, source, category_id, category_name, exists}
             `category_id` est None si le groupe n'a pas (encore) cette catégorie.
    """
    from .ml import suggest_category

    return suggest_category(text, group)


def _resolve_category(group, category):
    if category is None or category == "":
        return None
    from budgets.models import Category

    if isinstance(category, Category):
        return category if category.group_id == group.pk else None
    if isinstance(category, int) or (isinstance(category, str) and category.isdigit()):
        return Category.objects.filter(group=group, pk=int(category)).first()
    return Category.objects.filter(group=group, name__iexact=str(category).strip()).first()
