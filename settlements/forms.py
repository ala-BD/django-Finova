from datetime import timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.forms import AccessibleFormMixin

from .models import MILLIME, RepaymentPlan
from .services import TONES


class MembershipChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, membership):
        return membership.user.get_full_name() or membership.user.username


class SettlementForm(AccessibleFormMixin, forms.Form):
    """Le payeur est l'utilisateur connecté : il déclare avoir remboursé un autre membre."""

    receiver = MembershipChoiceField(
        label="Bénéficiaire",
        queryset=None,
        empty_label="— choisir un membre —",
    )
    amount = forms.DecimalField(
        label="Montant",
        max_digits=12,
        decimal_places=3,
        min_value=MILLIME,
        widget=forms.NumberInput(attrs={"step": "0.001", "min": "0.001", "inputmode": "decimal"}),
        error_messages={"min_value": "Le montant doit être strictement positif."},
    )
    note = forms.CharField(
        label="Note",
        max_length=200,
        required=False,
        help_text="Facultatif : virement, espèces, référence…",
    )

    def __init__(self, *args, group, payer, **kwargs):
        super().__init__(*args, **kwargs)
        self.payer = payer
        self.fields["receiver"].queryset = (
            group.active_memberships().exclude(pk=payer.pk).order_by("user__username")
        )
        self.fields["amount"].help_text = f"En {group.currency}, au millime près."

    def clean_receiver(self):
        receiver = self.cleaned_data["receiver"]
        if receiver.pk == self.payer.pk:
            raise ValidationError("Vous ne pouvez pas vous rembourser vous-même.")
        return receiver


class RepaymentPlanForm(AccessibleFormMixin, forms.ModelForm):
    class Meta:
        model = RepaymentPlan
        fields = ("installments_count", "frequency", "start_date")
        widgets = {
            "installments_count": forms.NumberInput(attrs={"min": "2", "max": "24"}),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        help_texts = {
            "installments_count": "Entre 2 et 24. Montants égaux, la dernière échéance absorbe l'arrondi.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.initial.setdefault("installments_count", 3)
            self.initial.setdefault("start_date", timezone.localdate() + timedelta(days=7))

    def clean_installments_count(self):
        count = self.cleaned_data["installments_count"]
        if count is None or count < 2:
            raise ValidationError("Il faut au moins 2 échéances.")
        return count

    def clean_start_date(self):
        start = self.cleaned_data["start_date"]
        if start < timezone.localdate():
            raise ValidationError("La première échéance ne peut pas être dans le passé.")
        return start


class InstallmentPaymentForm(AccessibleFormMixin, forms.Form):
    amount = forms.DecimalField(
        label="Montant payé",
        max_digits=12,
        decimal_places=3,
        min_value=MILLIME,
        widget=forms.NumberInput(attrs={"step": "0.001", "min": "0.001", "inputmode": "decimal"}),
        error_messages={"min_value": "Le paiement doit être strictement positif."},
    )

    def __init__(self, *args, installment, **kwargs):
        super().__init__(*args, **kwargs)
        self.installment = installment
        self.fields["amount"].help_text = (
            f"Paiement partiel autorisé, au plus {installment.remaining}."
        )
        self.fields["amount"].widget.attrs["max"] = str(installment.remaining)

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount > self.installment.remaining:
            raise ValidationError(
                f"Le paiement ne peut pas dépasser le reste dû ({self.installment.remaining})."
            )
        return amount


class ReminderForm(forms.Form):
    tone = forms.ChoiceField(
        label="Ton",
        choices=list(TONES.items()),
        initial="friendly",
        widget=forms.Select(attrs={"class": "input"}),
    )


class ReminderSendForm(forms.Form):
    message = forms.CharField(max_length=600, strip=True)
