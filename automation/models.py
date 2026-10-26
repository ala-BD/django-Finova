from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class RecurringExpense(models.Model):
    """Dépense récurrente au sein d'un groupe."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    FREQUENCY_CHOICES = (
        (WEEKLY, "Hebdomadaire"),
        (MONTHLY, "Mensuelle"),
        (YEARLY, "Annuelle"),
    )

    ACTIVE = "active"
    SUSPENDED = "suspended"
    STATUS_CHOICES = (
        (ACTIVE, "Actif"),
        (SUSPENDED, "Suspendu"),
    )

    group_id = models.PositiveBigIntegerField(db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="recurring_expenses",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.CharField(max_length=50, default="general")
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default=MONTHLY)
    start_date = models.DateField(default=timezone.localdate)
    next_run = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_run", "title"]
        verbose_name = "dépense récurrente"
        verbose_name_plural = "dépenses récurrentes"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="automation_recurring_amount_positive"),
            models.CheckConstraint(
                condition=Q(frequency__in=["weekly", "monthly", "yearly"]),
                name="automation_recurring_frequency_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["active", "suspended"]),
                name="automation_recurring_status_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Le montant doit être strictement positif."})
        if self.start_date and self.next_run and self.next_run < self.start_date:
            raise ValidationError(
                {"next_run": "La prochaine échéance ne peut pas précéder la date de début."}
            )
        if self.frequency not in dict(self.FREQUENCY_CHOICES):
            raise ValidationError({"frequency": "Fréquence invalide."})
        if self.status not in dict(self.STATUS_CHOICES):
            raise ValidationError({"status": "Statut invalide."})

    def __str__(self):
        return f"{self.title} ({self.frequency})"


class ReminderRule(models.Model):
    """Règle de rappel associée à une dépense récurrente."""

    CHANNEL_EMAIL = "email"
    CHANNEL_NOTIFICATION = "notification"
    CHANNEL_CHOICES = (
        (CHANNEL_EMAIL, "Email"),
        (CHANNEL_NOTIFICATION, "Notification"),
    )

    recurring_expense = models.ForeignKey(
        RecurringExpense,
        related_name="reminder_rules",
        on_delete=models.CASCADE,
    )
    days_before = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(0), MaxValueValidator(30)],
    )
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default=CHANNEL_NOTIFICATION)
    enabled = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["days_before", "channel"]
        verbose_name = "règle de rappel"
        verbose_name_plural = "règles de rappel"
        constraints = [
            models.CheckConstraint(
                condition=Q(days_before__gte=0, days_before__lte=30),
                name="automation_reminder_days_0_30",
            ),
            models.CheckConstraint(
                condition=Q(channel__in=["email", "notification"]),
                name="automation_reminder_channel_valid",
            ),
        ]

    def __str__(self):
        return f"{self.recurring_expense.title} · {self.days_before}j · {self.channel}"


class Notification(models.Model):
    """Notification interne de l'application."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="notifications", on_delete=models.CASCADE)
    kind = models.CharField(max_length=50, default="info")
    message = models.TextField()
    link = models.CharField(max_length=255, blank=True, default="")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "notification"
        verbose_name_plural = "notifications"

    def __str__(self):
        return f"{self.user} · {self.kind}"


class ChatMessage(models.Model):
    """Historique des messages de l'assistant."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="chat_messages", on_delete=models.CASCADE)
    question = models.TextField()
    answer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "message de chat"
        verbose_name_plural = "messages de chat"

    def __str__(self):
        return f"{self.user} · {self.created_at:%Y-%m-%d %H:%M}"


class RecurringOccurrence(models.Model):
    recurring_expense = models.ForeignKey(
        RecurringExpense,
        related_name="occurrences",
        on_delete=models.CASCADE,
    )
    due_date = models.DateField()
    generated_expense_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("recurring_expense", "due_date"),
                name="automation_unique_recurring_occurrence",
            ),
        ]
        ordering = ["-due_date"]


class ReminderDelivery(models.Model):
    rule = models.ForeignKey(ReminderRule, related_name="deliveries", on_delete=models.CASCADE)
    due_date = models.DateField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("rule", "due_date"), name="automation_unique_reminder_delivery"),
        ]
        ordering = ["-sent_at"]
