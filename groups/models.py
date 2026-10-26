import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Group(models.Model):
    class SplitRule(models.TextChoices):
        EQUAL = "equal", "Égale"
        BY_WEIGHT = "by_weight", "Proportionnelle au revenu"
        BY_CONSUMPTION = "by_consumption", "Proportionnelle à la consommation"

    class Currency(models.TextChoices):
        TND = "TND", "TND"
        EUR = "EUR", "EUR"
        USD = "USD", "USD"
        MAD = "MAD", "MAD"

    name = models.CharField(
        "nom",
        max_length=80,
        validators=[MinLengthValidator(3)],
    )
    description = models.TextField("description", blank=True)
    currency = models.CharField(
        "devise",
        max_length=3,
        choices=Currency.choices,
        default=Currency.TND,
    )
    default_split_rule = models.CharField(
        "règle de partage",
        max_length=32,
        choices=SplitRule.choices,
        default=SplitRule.EQUAL,
    )
    is_archived = models.BooleanField("archivé", default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_groups",
        verbose_name="créateur",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "groupe"
        verbose_name_plural = "groupes"

    def __str__(self):
        return self.name

    def has_expenses(self) -> bool:
        from django.apps import apps
        from django.core.exceptions import FieldError

        try:
            Expense = apps.get_model("expenses", "Expense")
        except LookupError:
            return False
        try:
            return Expense.objects.filter(group=self).exists()
        except (FieldError, AttributeError):
            return False

    def active_memberships(self):
        return self.memberships.filter(is_active=True).select_related("user")

    def admin_count(self) -> int:
        return self.active_memberships().filter(role=Membership.Role.ADMIN).count()


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Membre"

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="groupe",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_memberships",
        verbose_name="utilisateur",
    )
    role = models.CharField(
        "rôle",
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    weight = models.DecimalField(
        "poids",
        max_digits=8,
        decimal_places=3,
        default=Decimal("1.000"),
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    is_active = models.BooleanField("actif", default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-role", "user__username"]
        verbose_name = "appartenance"
        verbose_name_plural = "appartenances"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "user"],
                name="unique_member_per_group",
            ),
            models.CheckConstraint(
                condition=Q(weight__gt=0),
                name="weight_strictly_positive",
            ),
        ]

    def __str__(self):
        return f"{self.user} · {self.group}"

    @property
    def is_admin(self) -> bool:
        """Membre actif avec le rôle admin (utilisé par M2 : `membership.is_admin`)."""
        return self.is_active and self.role == self.Role.ADMIN

    def clean(self):
        if self.weight is not None and self.weight <= 0:
            raise ValidationError({"weight": "Le poids doit être strictement positif."})
        if self.pk and self.is_active:
            was_admin = (
                Membership.objects.filter(pk=self.pk, role=self.Role.ADMIN)
                .exclude(role=self.role)
                .exists()
            )
            becoming_non_admin = self.role != self.Role.ADMIN
            if was_admin and becoming_non_admin and self.group.admin_count() <= 1:
                raise ValidationError(
                    {"role": "Le groupe doit toujours conserver au moins un admin."}
                )


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
    email = models.EmailField("e-mail")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_invitations",
        verbose_name="invité par",
    )
    status = models.CharField(
        "statut",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    expires_at = models.DateTimeField("expire le")
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "invitation"
        verbose_name_plural = "invitations"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "email"],
                condition=Q(status="pending"),
                name="one_pending_invite_per_email",
            ),
        ]

    def __str__(self):
        return f"{self.email} → {self.group}"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def refresh_expiration_status(self):
        if self.status == self.Status.PENDING and self.is_expired():
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status"])
