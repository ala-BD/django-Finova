"""
Plan d'économie (M4) : répartir un effort d'épargne mensuel entre les catégories.

Fonctions pures (aucun accès à la base), appelées par budgets/services.py.

Principe, expliquable à l'oral :
1. Pour chaque catégorie, on mesure sur l'historique la dépense moyenne et son
   irrégularité (coefficient de variation = écart-type / moyenne).
2. Une dépense régulière (loyer, abonnement : variation proche de 0) est
   considérée comme fixe : on n'y touche pas. Une dépense irrégulière
   (restaurants, loisirs) est souple : c'est là qu'on peut économiser.
3. Plafond de réduction par catégorie = 2 × variation, au plus 30 % : on ne
   demande jamais de couper davantage que ce que la catégorie varie déjà
   naturellement d'un mois à l'autre.
4. L'effort est réparti au prorata de « moyenne × variation » ; si une catégorie
   atteint son plafond, le reste est redistribué aux autres (remplissage
   successif), jusqu'à couvrir l'objectif ou épuiser toutes les marges.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from statistics import mean, pstdev

from core.rounding import largest_remainder_rounding

MAX_CUT = Decimal("0.30")
FIXED_THRESHOLD = 0.05  # variation sous laquelle une dépense est considérée fixe
DEFAULT_VARIATION = 0.20  # une seule valeur d'historique : variation inconnue
MILLIME = Decimal("0.001")


@dataclass
class CategoryPlan:
    name: str
    average: Decimal
    variation: float
    flexibility: str
    max_reduction: Decimal
    reduction: Decimal = Decimal("0")

    @property
    def percent(self) -> Decimal:
        if not self.average:
            return Decimal("0")
        return (self.reduction * 100 / self.average).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    @property
    def new_target(self) -> Decimal:
        return self.average - self.reduction


@dataclass
class SavingsPlan:
    goal: Decimal
    months: int
    monthly_target: Decimal
    categories: list[CategoryPlan] = field(default_factory=list)
    feasible: bool = True
    max_monthly_saving: Decimal = Decimal("0")
    months_needed: int | None = None

    @property
    def monthly_saving(self) -> Decimal:
        return sum((category.reduction for category in self.categories), Decimal("0"))


def variation_of(totals) -> float:
    values = [float(total) for total in totals]
    if len(values) < 2:
        return DEFAULT_VARIATION
    average = mean(values)
    return pstdev(values) / average if average else 0.0


def flexibility_label(variation: float) -> str:
    if variation < FIXED_THRESHOLD:
        return "fixe"
    if variation < 0.15:
        return "peu flexible"
    return "flexible"


def plan_savings(history_by_category: dict, goal, months: int) -> SavingsPlan:
    """
    history_by_category : {nom de catégorie: [totaux mensuels passés]}
    goal : montant à économiser au total ; months : délai souhaité.
    """
    goal = Decimal(str(goal))
    monthly_target = (goal / months).quantize(MILLIME, rounding=ROUND_HALF_UP)
    categories = []
    for name, totals in history_by_category.items():
        if not totals or sum(totals) <= 0:
            continue
        average = (Decimal(str(sum(totals))) / len(totals)).quantize(MILLIME)
        variation = variation_of(totals)
        cap_ratio = Decimal("0") if variation < FIXED_THRESHOLD else min(MAX_CUT, Decimal(str(2 * variation)))
        categories.append(
            CategoryPlan(
                name=name,
                average=average,
                variation=round(variation, 3),
                flexibility=flexibility_label(variation),
                max_reduction=(average * cap_ratio).quantize(MILLIME),
            )
        )

    plan = SavingsPlan(goal=goal, months=months, monthly_target=monthly_target, categories=categories)
    plan.max_monthly_saving = sum((c.max_reduction for c in categories), Decimal("0"))

    # Remplissage successif : on répartit au prorata de moyenne × variation,
    # en plafonnant chaque catégorie, puis on redistribue le reste.
    raw = {c.name: Decimal("0") for c in categories}
    remaining = min(monthly_target, plan.max_monthly_saving)
    active = [c for c in categories if c.max_reduction > 0]
    while remaining > MILLIME / 2 and active:
        weights = {c.name: c.average * Decimal(str(c.variation)) for c in active}
        total_weight = sum(weights.values())
        given = Decimal("0")
        for category in active:
            share = remaining * weights[category.name] / total_weight
            room = category.max_reduction - raw[category.name]
            raw[category.name] += min(share, room)
            given += min(share, room)
        remaining -= given
        active = [c for c in active if raw[c.name] < c.max_reduction - MILLIME / 2]
        if given <= 0:
            break

    allocated = min(monthly_target, plan.max_monthly_saving)
    if raw and allocated > 0:
        rounded = largest_remainder_rounding(raw, allocated, places=3)
        for category in categories:
            category.reduction = min(rounded[category.name], category.max_reduction)

    plan.feasible = plan.max_monthly_saving >= monthly_target
    if not plan.feasible:
        plan.months_needed = (
            math.ceil(goal / plan.max_monthly_saving) if plan.max_monthly_saving > 0 else None
        )
    plan.categories.sort(key=lambda c: (-c.reduction, c.name))
    return plan
