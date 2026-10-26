from decimal import Decimal

from core.rounding import largest_remainder_rounding

from .models import Group, Membership

INCOME_WEIGHTS = {
    "student": Decimal("1.000"),
    "active": Decimal("2.000"),
    "senior": Decimal("3.000"),
}

SPLIT_RULE_HELP = {
    Group.SplitRule.EQUAL: "Chaque membre paie la même part.",
    Group.SplitRule.BY_WEIGHT: (
        "Chaque membre paie selon son poids (étudiant 1, actif 2, actif senior 3). "
        "Exemple : 90 TND avec 1, 2 et 3 → 15 / 30 / 45."
    ),
    Group.SplitRule.BY_CONSUMPTION: (
        "Chacun paie ce qu'il a consommé sur le ticket (M2). "
        "Tant qu'il n'y a pas de lignes de ticket, la répartition égale s'applique."
    ),
}


def is_member(user, group) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return group.memberships.filter(user=user, is_active=True).exists()


def is_admin(user, group) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return group.memberships.filter(
        user=user,
        is_active=True,
        role=Membership.Role.ADMIN,
    ).exists()


def default_weight_for(user) -> Decimal:
    profile = getattr(user, "income_profile", "active")
    return INCOME_WEIGHTS.get(profile, Decimal("1.000"))


def split_by_weight(amount, weights: dict) -> dict:
    amount = Decimal(str(amount))
    total = sum(Decimal(str(weight)) for weight in weights.values())
    if total <= 0:
        raise ValueError("La somme des poids doit être strictement positive.")
    raw = {member: amount * Decimal(str(weight)) / total for member, weight in weights.items()}
    return largest_remainder_rounding(raw, amount, places=3)


def split_equally(amount, member_ids: list) -> dict:
    amount = Decimal(str(amount))
    if not member_ids:
        return {}
    share = amount / len(member_ids)
    raw = {member_id: share for member_id in member_ids}
    return largest_remainder_rounding(raw, amount, places=3)


def consumption_totals(group) -> dict | None:
    """Totaux consommés par membre (M2). None si le module dépenses n'est pas prêt."""
    from django.apps import apps
    from django.core.exceptions import FieldError
    from django.db.models import Sum

    try:
        ItemClaim = apps.get_model("expenses", "ItemClaim")
    except LookupError:
        return None
    try:
        rows = (
            ItemClaim.objects.filter(item__expense__group=group)
            .values("user_id")
            .annotate(total=Sum("share_amount"))
        )
    except (FieldError, AttributeError):
        return None
    totals = {row["user_id"]: Decimal(str(row["total"] or 0)) for row in rows}
    if not totals or all(value == 0 for value in totals.values()):
        return None
    return totals


def preview_split(group, amount) -> dict:
    members = list(group.active_memberships())
    member_ids = [membership.user_id for membership in members]
    amount = Decimal(str(amount))
    note = SPLIT_RULE_HELP.get(group.default_split_rule, "")
    used_rule = group.default_split_rule

    if group.default_split_rule == Group.SplitRule.BY_WEIGHT:
        weights = {membership.user_id: membership.weight for membership in members}
        parts = split_by_weight(amount, weights)
    elif group.default_split_rule == Group.SplitRule.BY_CONSUMPTION:
        totals = consumption_totals(group)
        if totals:
            parts = split_by_weight(amount, totals)
        else:
            parts = split_equally(amount, member_ids)
            used_rule = Group.SplitRule.EQUAL
            note = (
                "Aucune consommation de ticket (M2) pour l'instant : "
                "répartition égale en repli."
            )
    else:
        parts = split_equally(amount, member_ids)

    rows = [
        {
            "membership": membership,
            "share": parts.get(membership.user_id, Decimal("0")),
            "weight": membership.weight,
        }
        for membership in members
    ]
    return {
        "amount": amount,
        "rows": rows,
        "note": note,
        "used_rule": used_rule,
    }


def get_member_balance(user, group) -> Decimal:
    try:
        from settlements.services import compute_balances

        balances = compute_balances(group)
    except (NotImplementedError, ImportError):
        return Decimal("0")
    if user in balances:
        return Decimal(str(balances[user]))
    if user.pk in balances:
        return Decimal(str(balances[user.pk]))
    return Decimal(str(balances.get(str(user.pk), 0)))


def can_leave(user, group) -> tuple[bool, str]:
    if get_member_balance(user, group) != 0:
        return False, "Impossible de quitter le groupe tant que votre solde n'est pas à zéro."
    membership = group.memberships.filter(user=user, is_active=True).first()
    if (
        membership
        and membership.role == Membership.Role.ADMIN
        and group.admin_count() <= 1
    ):
        return False, "Le dernier admin ne peut pas quitter le groupe."
    return True, ""
