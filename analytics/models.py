"""
Modèles M5 (Zahra) — gelés en S1 (intégration : Linda).

Tableaux de bord, exports, détection d'anomalies : analytics/services.py.
"""

from django.conf import settings
from django.db import models


class SavedFilter(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_filters",
        verbose_name="utilisateur",
    )
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="saved_filters",
        verbose_name="groupe",
    )
    name = models.CharField("nom", max_length=80)
    params = models.JSONField("paramètres", default=dict, blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

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
    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        PDF = "pdf", "PDF"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports",
        verbose_name="utilisateur",
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
    format = models.CharField("format", max_length=3, choices=Format.choices)
    file = models.FileField("fichier", upload_to="reports/", blank=True)
    narrative_summary = models.TextField("résumé narratif", blank=True)
    generated_at = models.DateTimeField("généré le", auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]
        verbose_name = "rapport"
        verbose_name_plural = "rapports"

    def __str__(self):
        return self.title


class AnomalyFlag(models.Model):
    class Kind(models.TextChoices):
        ANOMALY = "anomaly", "Anomalie"
        DUPLICATE = "duplicate", "Doublon"

    expense = models.ForeignKey(
        "expenses.Expense",
        on_delete=models.CASCADE,
        related_name="anomaly_flags",
        verbose_name="dépense",
    )
    kind = models.CharField("type", max_length=10, choices=Kind.choices)
    score = models.FloatField("score")
    reviewed = models.BooleanField("revu", default=False)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "signalement"
        verbose_name_plural = "signalements"

    def __str__(self):
        return f"{self.get_kind_display()} : {self.expense}"


class ExchangeRate(models.Model):
    base = models.CharField("devise de base", max_length=3)
    target = models.CharField("devise cible", max_length=3)
    rate = models.DecimalField("taux", max_digits=14, decimal_places=6)
    fetched_at = models.DateTimeField("récupéré le", auto_now=True)

    class Meta:
        verbose_name = "taux de change"
        verbose_name_plural = "taux de change"
        constraints = [
            models.UniqueConstraint(fields=["base", "target"], name="unique_exchange_pair"),
        ]

    def __str__(self):
        return f"1 {self.base} = {self.rate} {self.target}"
