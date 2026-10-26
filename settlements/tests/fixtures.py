from decimal import Decimal

from django.contrib.auth import get_user_model

from groups.models import Group, Membership
from settlements.models import Settlement

User = get_user_model()


def make_user(username, **extra):
    # Sans mot de passe : évite le hachage PBKDF2 (lent) ; les tests utilisent force_login
    return User.objects.create_user(username=username, email=f"{username}@example.com", **extra)


class SettlementFixturesMixin:
    """Groupe « Colocation » : Ala (admin), Yahya et Linda membres ; Imem hors du groupe."""

    def setUp(self):
        super().setUp()
        self.ala = make_user("ala", first_name="Ala")
        self.yahya = make_user("yahya", first_name="Yahya")
        self.linda = make_user("linda", first_name="Linda")
        self.imem = make_user("imem", first_name="Imem")
        self.group = Group.objects.create(name="Colocation", created_by=self.ala)
        self.m_ala = Membership.objects.create(
            group=self.group, user=self.ala, role=Membership.Role.ADMIN
        )
        self.m_yahya = Membership.objects.create(
            group=self.group, user=self.yahya, weight=Decimal("2.000")
        )
        self.m_linda = Membership.objects.create(group=self.group, user=self.linda)

        self.other_group = Group.objects.create(name="Voyage Djerba", created_by=self.imem)
        self.m_imem = Membership.objects.create(
            group=self.other_group, user=self.imem, role=Membership.Role.ADMIN
        )
        self.m_imem_ala = Membership.objects.create(group=self.other_group, user=self.ala)

    def settlement(self, payer=None, receiver=None, amount="60.000", **extra):
        return Settlement.objects.create(
            group=self.group,
            payer=payer or self.m_yahya,
            receiver=receiver or self.m_ala,
            amount=Decimal(amount),
            **extra,
        )
