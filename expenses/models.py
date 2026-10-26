"""
Modèles M2 (Yahya) — gelés en S1 (intégration : Linda).

Le calcul des parts (taxe et pourboire au prorata, plus grand reste) vit dans
expenses/services.py ; ces modèles ne fixent que la structure partagée.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from groups.models import SplitRule

MONEY = {"max_digits": 12, "decimal_places": 3}


class Expense(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Saisie manuelle"
        NATURAL_LANGUAGE = "nl", "Langage naturel"
        OCR = "ocr", "Lecture de ticket"

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="expenses",
        verbose_name="groupe",
    )
    payer = models.ForeignKey(
        "groups.Membership",
        on_delete=models.PROTECT,
        related_name="paid_expenses",
        verbose_name="payeur",
    )
    # PROTECT : une catégorie utilisée ne peut pas être supprimée (règle M4).
    category = models.ForeignKey(
        "budgets.Category",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name="catégorie",
    )
    title = models.CharField("libellé", max_length=120)
    amount = models.DecimalField(
        "montant", **MONEY, validators=[MinValueValidator(Decimal("0.001"))]
    )
    currency = models.CharField("devise", max_length=3, default="TND")
    expense_date = models.DateField("date")
    split_rule = models.CharField(
        "règle de partage", max_length=20, choices=SplitRule.choices, default=SplitRule.EQUAL
    )
    tax_amount = models.DecimalField("taxe", **MONEY, default=Decimal("0"))
    tip_amount = models.DecimalField("pourboire", **MONEY, default=Decimal("0"))
    receipt_image = models.ImageField("ticket", upload_to="receipts/", blank=True)
    source = models.CharField("source", max_length=10, choices=Source.choices, default=Source.MANUAL)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        ordering = ["-expense_date", "-created_at"]
        verbose_name = "dépense"
        verbose_name_plural = "dépenses"
        indexes = [models.Index(fields=["group", "category", "expense_date"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="expense_amount_positive"),
        ]

    def __str__(self):
        return f"{self.title} ({self.amount} {self.currency})"


class ExpenseItem(models.Model):
    expense = models.ForeignKey(
        Expense, on_delete=models.CASCADE, related_name="items", verbose_name="dépense"
    )
    label = models.CharField("libellé", max_length=120)
    unit_price = models.DecimalField(
        "prix unitaire", **MONEY, validators=[MinValueValidator(Decimal("0"))]
    )
    quantity = models.PositiveIntegerField("quantité", default=1, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "ligne de ticket"
        verbose_name_plural = "lignes de ticket"
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="item_quantity_min_1"),
        ]

    def __str__(self):
        return f"{self.label} × {self.quantity}"

    def total(self):
        return self.unit_price * self.quantity


class ItemClaim(models.Model):
    item = models.ForeignKey(
        ExpenseItem, on_delete=models.CASCADE, related_name="claims", verbose_name="ligne"
    )
    member = models.ForeignKey(
        "groups.Membership",
        on_delete=models.CASCADE,
        related_name="item_claims",
        verbose_name="membre",
    )
    portion = models.DecimalField(
        "portion", max_digits=5, decimal_places=4, default=Decimal("1")
    )

    class Meta:
        verbose_name = "réclamation d'article"
        verbose_name_plural = "réclamations d'articles"
        constraints = [
            models.UniqueConstraint(fields=["item", "member"], name="unique_claim_per_member"),
        ]

    def __str__(self):
        return f"{self.member} réclame {self.item}"


class ExpenseShare(models.Model):
    expense = models.ForeignKey(
        Expense, on_delete=models.CASCADE, related_name="shares", verbose_name="dépense"
    )
    member = models.ForeignKey(
        "groups.Membership",
        on_delete=models.CASCADE,
        related_name="expense_shares",
        verbose_name="membre",
    )
    amount_due = models.DecimalField("montant dû", **MONEY)

    class Meta:
        verbose_name = "part de dépense"
        verbose_name_plural = "parts de dépense"
        constraints = [
            models.UniqueConstraint(fields=["expense", "member"], name="unique_share_per_member"),
        ]

    def __str__(self):
        return f"{self.member} doit {self.amount_due}"
