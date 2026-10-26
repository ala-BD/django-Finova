"""
Modèles M4 (Linda) : Category ↔ Budget, et BudgetForecast pour la partie IA.

Les calculs (consommation, alertes, report, simulateur, prévision) vivent dans
budgets/services.py et budgets/forecasting.py.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower

hex_color_validator = RegexValidator(
    regex=r"^#[0-9a-fA-F]{6}$",
    message="Couleur au format hexadécimal, par exemple #0f5f59.",
)


class Category(models.Model):
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="categories",
        verbose_name="groupe",
    )
    name = models.CharField("nom", max_length=50)
    color = models.CharField(
        "couleur", max_length=7, default="#0f5f59", validators=[hex_color_validator]
    )
    icon = models.CharField(
        "icône",
        max_length=8,
        blank=True,
        help_text="Facultatif : un emoji, par exemple 🍽️.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "catégorie"
        verbose_name_plural = "catégories"
        constraints = [
            # Nom unique par groupe, sans tenir compte de la casse.
            models.UniqueConstraint(
                "group", Lower("name"), name="unique_category_name_per_group"
            ),
        ]

    def __str__(self):
        return f"{self.icon} {self.name}".strip()


class Budget(models.Model):
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="budgets",
        verbose_name="groupe",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="budgets",
        verbose_name="catégorie",
    )
    month = models.DateField("mois", help_text="Toujours le premier jour du mois.")
    limit_amount = models.DecimalField(
        "limite",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    carry_over = models.BooleanField(
        "reporter le reste",
        default=False,
        help_text="Le reste non dépensé s'ajoute à la limite du mois suivant.",
    )
    alert_80_sent = models.BooleanField("alerte 80 % envoyée", default=False)
    alert_100_sent = models.BooleanField("alerte 100 % envoyée", default=False)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["-month", "category__name"]
        verbose_name = "budget"
        verbose_name_plural = "budgets"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "month"], name="unique_budget_per_category_month"
            ),
            models.CheckConstraint(
                condition=models.Q(limit_amount__gt=0), name="budget_limit_positive"
            ),
        ]

    def __str__(self):
        return f"{self.category.name} · {self.month:%m/%Y}"

    def clean(self):
        if self.month and self.month.day != 1:
            raise ValidationError({"month": "Le mois doit commencer le premier jour."})
        if self.category_id and self.group_id and self.category.group_id != self.group_id:
            raise ValidationError({"category": "Cette catégorie n'appartient pas au groupe."})

    def consumed(self):
        from .services import consumed_amount

        return consumed_amount(self.group, self.category, self.month)

    def percent_consumed(self):
        from .services import get_budget_status

        return get_budget_status(self.group, self.category, self.month)["percent"]


class BudgetForecast(models.Model):
    """Prévision de fin de mois, gardée pour l'historique et la soutenance."""

    budget = models.ForeignKey(
        Budget,
        on_delete=models.CASCADE,
        related_name="forecasts",
        verbose_name="budget",
    )
    predicted_total = models.DecimalField("total prévu", max_digits=12, decimal_places=3)
    baseline_total = models.DecimalField(
        "prévision naïve", max_digits=12, decimal_places=3, null=True, blank=True
    )
    interval_low = models.DecimalField(
        "fourchette basse", max_digits=12, decimal_places=3, null=True, blank=True
    )
    interval_high = models.DecimalField(
        "fourchette haute", max_digits=12, decimal_places=3, null=True, blank=True
    )
    overrun_probability = models.FloatField("probabilité de dépassement", null=True, blank=True)
    method = models.CharField("méthode", max_length=32, blank=True)
    created_at = models.DateTimeField("calculée le", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "prévision de budget"
        verbose_name_plural = "prévisions de budget"

    def __str__(self):
        return f"{self.budget} → {self.predicted_total}"
