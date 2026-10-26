from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.forms import AccessibleFormMixin
from budgets.models import Category
from groups.models import Membership
from .models import Expense, ExpenseItem


class ExpenseForm(AccessibleFormMixin, forms.ModelForm):
    """Formulaire accessible de création et d'édition de dépense."""

    class Meta:
        model = Expense
        fields = [
            "title",
            "amount",
            "currency",
            "expense_date",
            "category",
            "payer",
            "split_rule",
            "tax_amount",
            "tip_amount",
            "receipt_image",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": _("Ex : Déjeuner d'équipe, Billets de train...")}),
            "amount": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
            "tax_amount": forms.NumberInput(attrs={"step": "0.001", "min": "0.000"}),
            "tip_amount": forms.NumberInput(attrs={"step": "0.001", "min": "0.000"}),
            "expense_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, group=None, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)

        if group is not None:
            self.fields["payer"].queryset = Membership.objects.filter(
                group=group, is_active=True
            ).select_related("user")
            self.fields["category"].queryset = Category.objects.filter(
                Q(group=group) | Q(group__isnull=True)
            ).distinct()

            if not self.instance.pk and not self.initial.get("currency"):
                self.fields["currency"].initial = group.currency
            if not self.instance.pk and not self.initial.get("expense_date"):
                self.fields["expense_date"].initial = timezone.localdate()

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= Decimal("0.000"):
            raise forms.ValidationError(_("Le montant doit être strictement supérieur à zéro."))
        return amount

    def clean_expense_date(self):
        expense_date = self.cleaned_data.get("expense_date")
        if expense_date and expense_date > timezone.localdate():
            raise forms.ValidationError(_("La date de la dépense ne peut pas être dans le futur."))
        return expense_date

    def clean(self):
        cleaned_data = super().clean()
        payer = cleaned_data.get("payer")
        if payer and self.group and payer.group_id != self.group.id:
            self.add_error("payer", _("Le payeur sélectionné n'appartient pas à ce groupe."))
        return cleaned_data


class ExpenseItemForm(AccessibleFormMixin, forms.ModelForm):
    """Formulaire d'ajout d'une ligne d'article de ticket."""

    class Meta:
        model = ExpenseItem
        fields = ["label", "unit_price", "quantity"]
        widgets = {
            "label": forms.TextInput(attrs={"placeholder": _("Ex : Pizza Margherita, Bouteille d'eau...")}),
            "unit_price": forms.NumberInput(attrs={"step": "0.001", "min": "0.000"}),
            "quantity": forms.NumberInput(attrs={"min": "1"}),
        }

    def clean_unit_price(self):
        price = self.cleaned_data.get("unit_price")
        if price is not None and price < Decimal("0.000"):
            raise forms.ValidationError(_("Le prix unitaire doit être positif."))
        return price

    def clean_quantity(self):
        qty = self.cleaned_data.get("quantity")
        if qty is not None and qty < 1:
            raise forms.ValidationError(_("La quantité doit être supérieure ou égale à 1."))
        return qty


class ExpenseFilterForm(forms.Form):
    """Filtres pour la liste des dépenses."""

    q = forms.CharField(
        label=_("Recherche"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Rechercher un libellé..."), "class": "input"}),
    )
    category = forms.ModelChoiceField(
        label=_("Catégorie"),
        queryset=Category.objects.none(),
        required=False,
        empty_label=_("Toutes les catégories"),
    )
    payer = forms.ModelChoiceField(
        label=_("Payé par"),
        queryset=Membership.objects.none(),
        required=False,
        empty_label=_("Tous les membres"),
    )
    period = forms.CharField(
        label=_("Période (AAAA-MM)"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "2026-10", "class": "input"}),
    )

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        if group is not None:
            self.fields["category"].queryset = Category.objects.filter(
                Q(group=group) | Q(group__isnull=True)
            ).distinct()
            self.fields["payer"].queryset = Membership.objects.filter(
                group=group, is_active=True
            ).select_related("user")
