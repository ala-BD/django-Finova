from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.rounding import largest_remainder_rounding


class Expense(models.Model):
    """M2 : Dépense principale partagée au sein d'un groupe (Yahya)."""

    class SplitRule(models.TextChoices):
        EQUAL = "equal", _("Égale")
        BY_WEIGHT = "by_weight", _("Proportionnelle au poids / revenu")
        BY_ITEM = "by_item", _("Par ligne de ticket")

    class Source(models.TextChoices):
        MANUAL = "manual", _("Saisie manuelle")
        NL = "nl", _("Langage naturel")
        OCR = "ocr", _("Ticket numérisé / OCR")

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="expenses",
        verbose_name=_("groupe"),
    )
    payer = models.ForeignKey(
        "groups.Membership",
        on_delete=models.PROTECT,
        related_name="expenses_paid",
        verbose_name=_("payeur"),
    )
    category = models.ForeignKey(
        "budgets.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name=_("catégorie"),
    )
    title = models.CharField(_("titre"), max_length=128)
    amount = models.DecimalField(
        _("montant"),
        max_digits=10,
        decimal_places=3,
    )
    currency = models.CharField(_("devise"), max_length=3, default="TND")
    expense_date = models.DateField(
        _("date de la dépense"),
        default=timezone.localdate,
    )
    split_rule = models.CharField(
        _("règle de partage"),
        max_length=20,
        choices=SplitRule.choices,
        default=SplitRule.EQUAL,
    )
    tax_amount = models.DecimalField(
        _("taxe"),
        max_digits=10,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    tip_amount = models.DecimalField(
        _("pourboire"),
        max_digits=10,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    receipt_image = models.ImageField(
        _("ticket joint"),
        upload_to="receipts/",
        blank=True,
    )
    source = models.CharField(
        _("source"),
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    created_at = models.DateTimeField(_("date de saisie"), auto_now_add=True)
    updated_at = models.DateTimeField(_("dernière modification"), auto_now=True)

    class Meta:
        verbose_name = _("dépense")
        verbose_name_plural = _("dépenses")
        ordering = ["-expense_date", "-created_at"]

    def __str__(self):
        return f"{self.title} — {self.amount} {self.currency} ({self.group.name})"

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= Decimal("0.000"):
            raise ValidationError({"amount": _("Le montant doit être strictement supérieur à zéro.")})
        if self.expense_date and self.expense_date > timezone.localdate():
            raise ValidationError({"expense_date": _("La date de la dépense ne peut pas être dans le futur.")})
        if self.payer_id and self.group_id and self.payer.group_id != self.group_id:
            raise ValidationError({"payer": _("Le payeur doit appartenir au groupe sélectionné.")})

    @property
    def items_total(self) -> Decimal:
        """Total des lignes d'articles de ticket associées."""
        return sum(
            (item.total for item in self.items.all()),
            Decimal("0.000"),
        )

    def can_delete(self) -> bool:
        """Suppression refusée si un remboursement lié est confirmé dans settlements."""
        try:
            from settlements.models import Settlement

            return not Settlement.objects.filter(
                group=self.group,
                status="confirmed",
            ).exists()
        except Exception:
            return True

    def compute_shares(self):
        """
        Calcule et persiste les parts dues par chaque membre (ExpenseShare).
        Garantit que la somme des parts est exactement égale au montant via
        largest_remainder_rounding.
        """
        active_members = list(
            self.group.memberships.filter(is_active=True).select_related("user")
        )
        if not active_members or self.amount <= Decimal("0.000"):
            return

        member_map = {m.id: m for m in active_members}

        if self.split_rule == self.SplitRule.EQUAL:
            n = Decimal(len(active_members))
            raw_shares = {m.id: self.amount / n for m in active_members}

        elif self.split_rule == self.SplitRule.BY_WEIGHT:
            total_weight = sum(m.weight for m in active_members)
            if total_weight <= Decimal("0.00"):
                total_weight = Decimal(len(active_members))
            raw_shares = {
                m.id: (self.amount * m.weight) / total_weight
                for m in active_members
            }

        elif self.split_rule == self.SplitRule.BY_ITEM:
            items = list(self.items.all().prefetch_related("claims"))
            if not items:
                # Si aucune ligne saisie, partage égal de repli
                n = Decimal(len(active_members))
                raw_shares = {m.id: self.amount / n for m in active_members}
            else:
                member_subtotals = {m.id: Decimal("0.000") for m in active_members}
                unclaimed_total = Decimal("0.000")

                for item in items:
                    item_total = item.total
                    claims = list(item.claims.all())
                    if claims:
                        # Article partagé à parts égales entre les réclamants
                        claimant_count = Decimal(len(claims))
                        part = item_total / claimant_count
                        for claim in claims:
                            if claim.member_id in member_subtotals:
                                member_subtotals[claim.member_id] += part
                    else:
                        unclaimed_total += item_total

                # Les articles non réclamés sont répartis à parts égales
                if unclaimed_total > Decimal("0.000"):
                    per_member_unclaimed = unclaimed_total / Decimal(len(active_members))
                    for mid in member_subtotals:
                        member_subtotals[mid] += per_member_unclaimed

                total_items = sum(member_subtotals.values())
                tax_and_tip = self.tax_amount + self.tip_amount

                raw_shares = {}
                for mid, s_i in member_subtotals.items():
                    if total_items > Decimal("0.000") and tax_and_tip > Decimal("0.000"):
                        # Formule spec M2 : part_i = S_i + (T+P) * S_i / Sigma_S
                        share = s_i + (tax_and_tip * (s_i / total_items))
                    else:
                        share = s_i
                    raw_shares[mid] = share

        else:
            n = Decimal(len(active_members))
            raw_shares = {m.id: self.amount / n for m in active_members}

        # Application de l'arrondi au millime exact (Hare-Niemeyer)
        rounded_shares = largest_remainder_rounding(raw_shares, self.amount, places=3)

        # Enregistrement en base de données
        self.shares.all().delete()
        shares_to_create = [
            ExpenseShare(
                expense=self,
                member=member_map[mid],
                amount_due=amount_due,
            )
            for mid, amount_due in rounded_shares.items()
        ]
        ExpenseShare.objects.bulk_create(shares_to_create)


class ExpenseItem(models.Model):
    """M2 : Ligne de ticket détaillée d'une dépense (Yahya)."""

    expense = models.ForeignKey(
        Expense,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("dépense"),
    )
    label = models.CharField(_("libellé"), max_length=128)
    unit_price = models.DecimalField(
        _("prix unitaire"),
        max_digits=10,
        decimal_places=3,
    )
    quantity = models.PositiveIntegerField(_("quantité"), default=1)

    class Meta:
        verbose_name = _("ligne de ticket")
        verbose_name_plural = _("lignes de ticket")
        ordering = ["id"]

    def __str__(self):
        return f"{self.label} ({self.quantity} x {self.unit_price} {self.expense.currency})"

    @property
    def total(self) -> Decimal:
        return self.unit_price * Decimal(self.quantity)

    def clean(self):
        super().clean()
        if self.unit_price is not None and self.unit_price < Decimal("0.000"):
            raise ValidationError({"unit_price": _("Le prix unitaire doit être positif.")})
        if self.quantity is not None and self.quantity < 1:
            raise ValidationError({"quantity": _("La quantité doit être au moins de 1.")})


class ItemClaim(models.Model):
    """M2 : Réclamation d'un article par un membre (Yahya)."""

    item = models.ForeignKey(
        ExpenseItem,
        on_delete=models.CASCADE,
        related_name="claims",
        verbose_name=_("article"),
    )
    member = models.ForeignKey(
        "groups.Membership",
        on_delete=models.CASCADE,
        related_name="item_claims",
        verbose_name=_("membre"),
    )
    portion = models.DecimalField(
        _("portion"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("1.00"),
    )

    class Meta:
        verbose_name = _("réclamation d'article")
        verbose_name_plural = _("réclamations d'articles")
        unique_together = ("item", "member")

    def __str__(self):
        return f"{self.member.user.username} réclame {self.item.label}"


class ExpenseShare(models.Model):
    """M2 : Part calculée due par un membre pour une dépense donnée (Yahya)."""

    expense = models.ForeignKey(
        Expense,
        on_delete=models.CASCADE,
        related_name="shares",
        verbose_name=_("dépense"),
    )
    member = models.ForeignKey(
        "groups.Membership",
        on_delete=models.CASCADE,
        related_name="expense_shares",
        verbose_name=_("membre débiteur"),
    )
    amount_due = models.DecimalField(
        _("part due"),
        max_digits=10,
        decimal_places=3,
    )

    class Meta:
        verbose_name = _("part de dépense")
        verbose_name_plural = _("parts de dépenses")
        unique_together = ("expense", "member")

    def __str__(self):
        return f"{self.member.user.username} doit {self.amount_due} {self.expense.currency} pour {self.expense.title}"
