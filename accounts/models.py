from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models


class User(AbstractUser):
    """Utilisateur Finova (AUTH_USER_MODEL = accounts.User), figé dès la première migration."""

    class IncomeProfile(models.TextChoices):
        STUDENT = "student", "Étudiant"
        ACTIVE = "active", "Actif"
        SENIOR = "senior", "Actif senior"

    avatar = models.ImageField("avatar", upload_to="avatars/", blank=True)
    preferred_currency = models.CharField("devise préférée", max_length=3, default="TND")
    income_profile = models.CharField(
        "profil de revenu",
        max_length=20,
        choices=IncomeProfile.choices,
        default=IncomeProfile.ACTIVE,
    )
    monthly_income = models.DecimalField(
        "revenu mensuel",
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Facultatif. Sert à proposer un poids pour la règle d'équité (M1).",
    )

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"

    def __str__(self):
        return self.get_full_name() or self.username

    def get_groups(self):
        """Groupes actifs (non archivés) dont l'utilisateur est membre actif."""
        from groups.models import Group

        return Group.objects.filter(
            memberships__user=self,
            memberships__is_active=True,
            is_archived=False,
        ).distinct()
