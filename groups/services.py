"""Contrats M1 : is_member / is_admin. Implémentés par Ala après gel du modèle."""


def is_member(user, group) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    memberships = getattr(group, "memberships", None)
    if memberships is None:
        return False
    return memberships.filter(user=user).exists()


def is_admin(user, group) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    memberships = getattr(group, "memberships", None)
    if memberships is None:
        return False
    return memberships.filter(user=user, role="admin").exists()
