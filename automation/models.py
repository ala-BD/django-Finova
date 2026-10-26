"""
Modèles M6 (Wassim) — gelés en S1 (intégration : Linda).

Génération des récurrentes, rappels, assistant : automation/services.py.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class RecurringExpense(models.Model):
    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "Hebdomadaire"
        MONTHLY = "monthly", "Mensuelle"
        YEARLY = "yearly", "Annuelle"

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="recurring_expenses",
        verbose_name="groupe",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recurring_expenses",
        verbose_name="créée par",
    )
    category = models.ForeignKey(
        "budgets.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recurring_expenses",
        verbose_name="catégorie",
    )
    title = models.CharField("libellé", max_length=120)
    amount = models.DecimalField(
        "montant", max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )
    frequency = models.CharField("fréquence", max_length=10, choices=Frequency.choices)
    next_run = models.DateField("prochaine génération")
    is_active = models.BooleanField("active", default=True)

    class Meta:
        ordering = ["next_run"]
        verbose_name = "dépense récurrente"
        verbose_name_plural = "dépenses récurrentes"

    def __str__(self):
        return f"{self.title} ({self.get_frequency_display()})"


class ReminderRule(models.Model):
    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        IN_APP = "in_app", "Dans l'application"

    recurring = models.ForeignKey(
        RecurringExpense,
        on_delete=models.CASCADE,
        related_name="reminder_rules",
        verbose_name="dépense récurrente",
    )
    days_before = models.PositiveSmallIntegerField(
        "jours avant", validators=[MaxValueValidator(30)]
    )
    channel = models.CharField("canal", max_length=10, choices=Channel.choices, default=Channel.IN_APP)
    is_active = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "règle de rappel"
        verbose_name_plural = "règles de rappel"

    def __str__(self):
        return f"{self.recurring} : J-{self.days_before}"


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="utilisateur",
    )
    kind = models.CharField("type", max_length=32)
    message = models.CharField("message", max_length=255)
    link = models.CharField("lien", max_length=255, blank=True)
    is_read = models.BooleanField("lue", default=False)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "notification"
        verbose_name_plural = "notifications"
        indexes = [models.Index(fields=["user", "is_read"])]

    def __str__(self):
        return self.message


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Utilisateur"
        ASSISTANT = "assistant", "Assistant"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        verbose_name="utilisateur",
    )
    role = models.CharField("rôle", max_length=10, choices=Role.choices)
    content = models.TextField("contenu")
    function_name = models.CharField("fonction appelée", max_length=64, blank=True)
    latency_ms = models.PositiveIntegerField("latence (ms)", null=True, blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "message de l'assistant"
        verbose_name_plural = "messages de l'assistant"

    def __str__(self):
        return f"{self.get_role_display()} : {self.content[:40]}"
