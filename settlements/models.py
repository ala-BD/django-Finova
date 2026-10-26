"""
Modèles M3 (Imem) — gelés en S1 (intégration : Linda).

Soldes, simplification des dettes et échéancier : settlements/services.py.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

MONEY = {"max_digits": 12, "decimal_places": 3}


class Settlement(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        CONFIRMED = "confirmed", "Confirmé"
        REJECTED = "rejected", "Rejeté"

    group = models.ForeignKey(
        "groups.Group", on_delete=models.CASCADE, related_name="settlements", verbose_name="groupe"
    )
    payer = models.ForeignKey(
        "groups.Membership",
        on_delete=models.PROTECT,
        related_name="settlements_paid",
        verbose_name="payeur",
    )
    receiver = models.ForeignKey(
        "groups.Membership",
        on_delete=models.PROTECT,
        related_name="settlements_received",
        verbose_name="bénéficiaire",
    )
    amount = models.DecimalField(
        "montant", **MONEY, validators=[MinValueValidator(Decimal("0.001"))]
    )
    status = models.CharField("statut", max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    confirmed_at = models.DateTimeField("confirmé le", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "remboursement"
        verbose_name_plural = "remboursements"
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="settlement_amount_positive"),
            models.CheckConstraint(
                condition=~models.Q(payer=models.F("receiver")), name="settlement_payer_not_receiver"
            ),
        ]

    def __str__(self):
        return f"{self.payer} → {self.receiver} : {self.amount}"


class Frequency(models.TextChoices):
    WEEKLY = "weekly", "Hebdomadaire"
    MONTHLY = "monthly", "Mensuelle"


class RepaymentPlan(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "En cours"
        COMPLETED = "completed", "Terminé"
        CANCELLED = "cancelled", "Annulé"

    settlement = models.OneToOneField(
        Settlement, on_delete=models.CASCADE, related_name="plan", verbose_name="remboursement"
    )
    total_amount = models.DecimalField("montant total", **MONEY)
    installments_count = models.PositiveSmallIntegerField(
        "nombre d'échéances", validators=[MinValueValidator(2)]
    )
    frequency = models.CharField("fréquence", max_length=10, choices=Frequency.choices)
    start_date = models.DateField("date de début")
    status = models.CharField("statut", max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = "échéancier"
        verbose_name_plural = "échéanciers"

    def __str__(self):
        return f"Échéancier {self.settlement}"


class Installment(models.Model):
    class Status(models.TextChoices):
        DUE = "due", "À payer"
        PARTIAL = "partial", "Partiellement payée"
        PAID = "paid", "Payée"

    plan = models.ForeignKey(
        RepaymentPlan, on_delete=models.CASCADE, related_name="installments", verbose_name="échéancier"
    )
    due_date = models.DateField("échéance")
    amount = models.DecimalField("montant", **MONEY)
    paid_amount = models.DecimalField("montant payé", **MONEY, default=Decimal("0"))
    status = models.CharField("statut", max_length=10, choices=Status.choices, default=Status.DUE)

    class Meta:
        ordering = ["due_date"]
        verbose_name = "échéance"
        verbose_name_plural = "échéances"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(paid_amount__lte=models.F("amount")), name="installment_not_overpaid"
            ),
        ]

    def __str__(self):
        return f"{self.due_date} : {self.amount}"


class RiskPrediction(models.Model):
    class Label(models.TextChoices):
        LOW = "low", "Faible"
        HIGH = "high", "Élevé"

    plan = models.ForeignKey(
        RepaymentPlan, on_delete=models.CASCADE, related_name="risk_predictions", verbose_name="échéancier"
    )
    probability = models.FloatField("probabilité")
    label = models.CharField("niveau", max_length=10, choices=Label.choices)
    model_version = models.CharField("version du modèle", max_length=32)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "prédiction de risque"
        verbose_name_plural = "prédictions de risque"

    def __str__(self):
        return f"{self.plan} : {self.probability:.2f}"
