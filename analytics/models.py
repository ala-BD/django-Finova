from decimal import Decimal

from django.conf import settings
from django.db import models


class SavedFilter(models.Model):
    """Requête de dépenses mémorisée par un utilisateur (paramètres en JSON)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_filters",
    )
    # Le groupe est stocké dans params["group"] tant que groups.Group n'est pas figé.
    # Après le gel du modèle : ajouter group = FK(groups.Group, null=True).
    name = models.CharField("nom", max_length=80)
    params = models.JSONField("paramètres", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "filtre sauvegardé"
        verbose_name_plural = "filtres sauvegardés"
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_filter_name_per_user"),
        ]

    def __str__(self):
        return self.name


class Report(models.Model):
    """Résultat exporté (CSV ou PDF) d'un filtre."""

    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        PDF = "pdf", "PDF"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    saved_filter = models.ForeignKey(
        SavedFilter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
        verbose_name="filtre",
    )
    title = models.CharField("titre", max_length=120)
    format = models.CharField("format", max_length=3, choices=Format.choices, default=Format.CSV)
    params = models.JSONField("paramètres utilisés", default=dict, blank=True)
    file = models.FileField("fichier", upload_to="reports/%Y/%m/", blank=True)
    narrative_summary = models.TextField("résumé du mois", blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]
        verbose_name = "rapport"
        verbose_name_plural = "rapports"

    def __str__(self):
        return f"{self.title} ({self.get_format_display()})"

    @property
    def content_type(self):
        if self.format == self.Format.PDF:
            return "application/pdf"
        return "text/csv; charset=utf-8"

    @property
    def download_name(self):
        return f"finova-rapport-{self.pk}.{self.format}"


class AnomalyFlag(models.Model):
    """Dépense signalée comme anomalie ou doublon, que l'utilisateur peut marquer comme revue."""

    class Kind(models.TextChoices):
        ANOMALY = "anomaly", "Anomalie"
        DUPLICATE = "duplicate", "Doublon"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="anomaly_flags",
    )
    # Identifiant de la dépense M2. Deviendra FK(expenses.Expense) après le gel du modèle.
    expense_ref = models.PositiveBigIntegerField("dépense")
    expense_label = models.CharField("libellé", max_length=200)
    expense_amount = models.DecimalField("montant", max_digits=12, decimal_places=3)
    currency = models.CharField("devise", max_length=3, default="TND")
    expense_date = models.DateField("date de la dépense")
    kind = models.CharField("type", max_length=10, choices=Kind.choices)
    score = models.FloatField("score")
    reason = models.CharField("explication", max_length=255, blank=True)
    reviewed = models.BooleanField("revue", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["reviewed", "-score"]
        verbose_name = "anomalie détectée"
        verbose_name_plural = "anomalies détectées"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "expense_ref", "kind"], name="unique_flag_per_expense_and_kind"
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} : {self.expense_label}"


class ExchangeRate(models.Model):
    """Dernier taux connu base → cible (repli quand l'API de taux est indisponible)."""

    base = models.CharField(max_length=3)
    target = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["base", "target"]
        verbose_name = "taux de change"
        verbose_name_plural = "taux de change"
        constraints = [
            models.UniqueConstraint(fields=["base", "target"], name="unique_rate_pair"),
        ]

    def __str__(self):
        return f"1 {self.base} = {self.rate} {self.target}"

    def convert(self, amount) -> Decimal:
        return (Decimal(amount) * self.rate).quantize(Decimal("0.001"))
