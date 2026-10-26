from decimal import Decimal

from core.rounding import largest_remainder_rounding

from .models import Membership

INCOME_WEIGHTS = {
    "student": Decimal("1.000"),
    "active": Decimal("2.000"),
    "senior": Decimal("3.000"),
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
