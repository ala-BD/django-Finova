"""
Modèles M1 (Ala) — gelés en S1 (intégration : Linda).

La logique métier (invitations, règle de partage, quitter un groupe) vit dans
groups/services.py ; ces modèles ne fixent que la structure partagée.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinLengthValidator, MinValueValidator
from django.db import models
from django.utils import timezone

INVITATION_VALIDITY = timedelta(days=7)


class SplitRule(models.TextChoices):
    EQUAL = "equal", "Égale"
    WEIGHTED = "weighted", "Proportionnelle au revenu (poids)"
    CONSUMPTION = "consumption", "Proportionnelle à la consommation"


class Group(models.Model):
    name = models.CharField("nom", max_length=80, validators=[MinLengthValidator(3)])
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
        on_delete=models.PROTECT,
        related_name="created_groups",
        verbose_name="créé par",
    )
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "groupe"
        verbose_name_plural = "groupes"

    def __str__(self):
        return self.name


class Membership(models.Model):
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
    role = models.CharField("rôle", max_length=10, choices=Role.choices, default=Role.MEMBER)
    weight = models.DecimalField(
        "poids",
        max_digits=6,
        decimal_places=2,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    is_active = models.BooleanField("actif", default=True)
    joined_at = models.DateTimeField("rejoint le", auto_now_add=True)

    class Meta:
        ordering = ["group", "user__username"]
        verbose_name = "adhésion"
        verbose_name_plural = "adhésions"
        constraints = [
            models.UniqueConstraint(fields=["user", "group"], name="unique_membership_per_group"),
            models.CheckConstraint(condition=models.Q(weight__gt=0), name="membership_weight_positive"),
        ]

    def __str__(self):
        return f"{self.user} · {self.group}"

    def is_admin(self):
        return self.role == self.Role.ADMIN


def default_invitation_expiry():
    return timezone.now() + INVITATION_VALIDITY


class Invitation(models.Model):
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
    email = models.EmailField("email")
    token = models.UUIDField("jeton", default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField("statut", max_length=10, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField("expire le", default=default_invitation_expiry)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_invitations",
        verbose_name="invité par",
    )
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "invitation"
        verbose_name_plural = "invitations"

    def __str__(self):
        return f"{self.email} → {self.group}"

    def is_valid(self):
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()
