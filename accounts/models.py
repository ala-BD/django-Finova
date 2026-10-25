from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Utilisateur Finova. À figer dès la première migration (Linda / auth)."""

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

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"

    def __str__(self):
        return self.get_full_name() or self.username
