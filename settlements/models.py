import calendar
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

MILLIME = Decimal("0.001")
LATE_AFTER_DAYS = 3


class Settlement(models.Model):
    """Remboursement déclaré par le payeur, confirmé ou rejeté par le bénéficiaire."""

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        CONFIRMED = "confirmed", "Confirmé"
        REJECTED = "rejected", "Rejeté"

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="settlements",
        verbose_name="groupe",
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
        "montant",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(MILLIME)],
    )
    note = models.CharField("note", max_length=200, blank=True)
    status = models.CharField(
        "statut",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField("confirmé le", null=True, blank=True)
    rejected_at = models.DateTimeField("rejeté le", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "remboursement"
        verbose_name_plural = "remboursements"
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="settlement_amount_positive",
            ),
            models.CheckConstraint(
                condition=~Q(payer=F("receiver")),
                name="settlement_payer_not_receiver",
            ),
        ]

    def __str__(self):
        return f"{self.payer.user} → {self.receiver.user} : {self.amount} {self.group.currency}"

    def clean(self):
        errors = {}
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "Le montant doit être strictement positif."
        if self.payer_id and self.receiver_id:
            if self.payer_id == self.receiver_id:
                errors["receiver"] = "Le bénéficiaire doit être différent du payeur."
            for field in ("payer", "receiver"):
                membership = getattr(self, field)
                if membership.group_id != self.group_id or not membership.is_active:
                    errors[field] = "Doit être un membre actif du groupe."
        if errors:
            raise ValidationError(errors)

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    def confirm(self):
        """Transition irréversible pending → confirmed (contrôle d'accès dans services)."""
        if not self.is_pending:
            raise ValidationError("Ce remboursement a déjà été traité.")
        self.status = self.Status.CONFIRMED
        self.confirmed_at = timezone.now()
        self.save(update_fields=["status", "confirmed_at"])

    def reject(self):
        if not self.is_pending:
            raise ValidationError("Ce remboursement a déjà été traité.")
        self.status = self.Status.REJECTED
        self.rejected_at = timezone.now()
        self.save(update_fields=["status", "rejected_at"])


def add_months(day, months):
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


class RepaymentPlan(models.Model):
    """Échéancier : le montant du remboursement réparti en n échéances."""

    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "Hebdomadaire"
        BIWEEKLY = "biweekly", "Toutes les deux semaines"
        MONTHLY = "monthly", "Mensuelle"

    class Status(models.TextChoices):
        ACTIVE = "active", "En cours"
        COMPLETED = "completed", "Terminé"
        CANCELLED = "cancelled", "Annulé"

    settlement = models.OneToOneField(
        Settlement,
        on_delete=models.CASCADE,
        related_name="plan",
        verbose_name="remboursement",
    )
    total_amount = models.DecimalField("montant total", max_digits=12, decimal_places=3)
    installments_count = models.PositiveSmallIntegerField(
        "nombre d'échéances",
        validators=[MinValueValidator(2), MaxValueValidator(24)],
    )
    frequency = models.CharField(
        "fréquence",
        max_length=20,
        choices=Frequency.choices,
        default=Frequency.MONTHLY,
    )
    start_date = models.DateField("première échéance")
    status = models.CharField(
        "statut",
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "échéancier"
        verbose_name_plural = "échéanciers"
        constraints = [
            models.CheckConstraint(
                condition=Q(installments_count__gte=2),
                name="plan_at_least_two_installments",
            ),
        ]

    def __str__(self):
        return f"Échéancier de {self.settlement}"

    def due_date(self, index):
        if self.frequency == self.Frequency.WEEKLY:
            return self.start_date + timedelta(weeks=index)
        if self.frequency == self.Frequency.BIWEEKLY:
            return self.start_date + timedelta(weeks=2 * index)
        return add_months(self.start_date, index)

    def split_amounts(self):
        """Montants égaux au millime inférieur ; la dernière échéance absorbe le reste."""
        base = (self.total_amount / self.installments_count).quantize(MILLIME, rounding=ROUND_DOWN)
        last = self.total_amount - base * (self.installments_count - 1)
        return [base] * (self.installments_count - 1) + [last]

    def generate_installments(self):
        self.installments.all().delete()
        Installment.objects.bulk_create(
            Installment(plan=self, number=i + 1, due_date=self.due_date(i), amount=amount)
            for i, amount in enumerate(self.split_amounts())
        )

    def has_payments(self):
        return self.installments.filter(paid_amount__gt=0).exists()

    @property
    def paid_amount(self):
        return sum((i.paid_amount for i in self.installments.all()), Decimal("0"))

    @property
    def remaining_amount(self):
        return self.total_amount - self.paid_amount

    def latest_prediction(self):
        return self.risk_predictions.order_by("-created_at").first()


class Installment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "À payer"
        PARTIAL = "partial", "Partiellement payée"
        PAID = "paid", "Payée"

    plan = models.ForeignKey(
        RepaymentPlan,
        on_delete=models.CASCADE,
        related_name="installments",
        verbose_name="échéancier",
    )
    number = models.PositiveSmallIntegerField("n°")
    due_date = models.DateField("échéance")
    amount = models.DecimalField("montant dû", max_digits=12, decimal_places=3)
    paid_amount = models.DecimalField(
        "montant payé",
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
    )
    status = models.CharField(
        "statut",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    paid_at = models.DateField("payée le", null=True, blank=True)

    class Meta:
        ordering = ["plan", "number"]
        verbose_name = "échéance"
        verbose_name_plural = "échéances"
        constraints = [
            models.UniqueConstraint(fields=["plan", "number"], name="unique_installment_number"),
            models.CheckConstraint(
                condition=Q(paid_amount__gte=0) & Q(paid_amount__lte=F("amount")),
                name="installment_paid_within_amount",
            ),
        ]

    def __str__(self):
        return f"Échéance {self.number} — {self.amount}"

    @property
    def remaining(self):
        return self.amount - self.paid_amount

    def days_late(self, today=None):
        """Retard en jours : à la date de paiement complet, ou aujourd'hui si non soldée."""
        reference = self.paid_at or today or timezone.localdate()
        return max((reference - self.due_date).days, 0)

    def is_late(self, today=None):
        return self.days_late(today) > LATE_AFTER_DAYS

    def register_payment(self, amount, today=None):
        """Paiement partiel autorisé, jamais au-delà du montant dû."""
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValidationError("Le paiement doit être strictement positif.")
        if amount > self.remaining:
            raise ValidationError(
                f"Le paiement dépasse le reste dû ({self.remaining})."
            )
        self.paid_amount += amount
        if self.paid_amount == self.amount:
            self.status = self.Status.PAID
            self.paid_at = today or timezone.localdate()
        else:
            self.status = self.Status.PARTIAL
        self.save(update_fields=["paid_amount", "status", "paid_at"])
        plan = self.plan
        if not plan.installments.exclude(status=self.Status.PAID).exists():
            plan.status = RepaymentPlan.Status.COMPLETED
            plan.save(update_fields=["status"])


class RiskPrediction(models.Model):
    """Probabilité de retard (> 3 jours) prédite par le modèle de régression logistique."""

    class Label(models.TextChoices):
        LOW = "low", "Risque faible"
        HIGH = "high", "Risque élevé"

    plan = models.ForeignKey(
        RepaymentPlan,
        on_delete=models.CASCADE,
        related_name="risk_predictions",
        verbose_name="échéancier",
    )
    probability = models.FloatField("probabilité")
    label = models.CharField("niveau", max_length=10, choices=Label.choices)
    model_version = models.CharField("version du modèle", max_length=40)
    features = models.JSONField("variables", default=dict)
    latency_ms = models.PositiveIntegerField("temps de calcul (ms)", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "prédiction de risque"
        verbose_name_plural = "prédictions de risque"

    def __str__(self):
        return f"{self.get_label_display()} ({self.probability:.0%})"

    @property
    def percent(self):
        return round(self.probability * 100)
