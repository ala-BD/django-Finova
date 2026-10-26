import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Group(models.Model):
    """M1 : Groupe de partage de dépenses (Ala)."""

    class SplitRule(models.TextChoices):
        EQUAL = "equal", "Égale"
        BY_WEIGHT = "by_weight", "Proportionnelle au poids"
        BY_ITEM = "by_item", "Par ligne de ticket"

    name = models.CharField("nom", max_length=80)
    description = models.TextField("description", blank=True)
    currency = models.CharField("devise", max_length=3, default="TND")
    default_split_rule = models.CharField(
        "règle de partage par défaut",
        max_length=20,
        choices=SplitRule.choices,
        default=SplitRule.EQUAL,
    )
    is_archived = models.BooleanField("archivé", default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_groups",
        verbose_name="créé par",
    )
    created_at = models.DateTimeField("date de création", auto_now_add=True)

    class Meta:
        verbose_name = "groupe"
        verbose_name_plural = "groupes"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def can_leave(self, member) -> bool:
        """Interdit de quitter le groupe si le solde est non nul."""
        try:
            from settlements.services import compute_balances

            balances = compute_balances(self)
            return balances.get(member, Decimal("0.000")) == Decimal("0.000")
        except Exception:
            return True


class Membership(models.Model):
    """Adhésion d'un utilisateur à un groupe avec rôle et pondération d'équité."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrateur"
        MEMBER = "member", "Membre"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="utilisateur",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="groupe",
    )
    role = models.CharField(
        "rôle",
        max_length=10,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    weight = models.DecimalField(
        "poids d'équité",
        max_digits=5,
        decimal_places=2,
        default=Decimal("1.00"),
    )
    is_active = models.BooleanField("actif", default=True)
    joined_at = models.DateTimeField("rejoint le", auto_now_add=True)

    class Meta:
        verbose_name = "adhésion"
        verbose_name_plural = "adhésions"
        unique_together = ("user", "group")

    def __str__(self):
        name = self.user.get_full_name() or self.user.username
        return f"{name} ({self.group.name})"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN


class Invitation(models.Model):
    """Invitation par jeton temporaire expirant (7 jours)."""

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        ACCEPTED = "accepted", "Acceptée"
        EXPIRED = "expired", "Expirée"

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="invitations",
        verbose_name="groupe",
    )
    email = models.EmailField("email invité")
    token = models.UUIDField("jeton", default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(
        "statut",
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    expires_at = models.DateTimeField("expire le")
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_invitations",
        verbose_name="invité par",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "invitation"
        verbose_name_plural = "invitations"

    def is_valid(self):
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()
