from django import forms

from accounts.forms import AccessibleFormMixin
from analytics.currency import SUPPORTED_CURRENCIES
from analytics.models import Report, SavedFilter
from analytics.services import params_to_json


class ExpenseFilterForm(AccessibleFormMixin, forms.Form):
    """Filtres du tableau de bord. Les choix proviennent des seules dépenses de l'utilisateur."""

    start = forms.DateField(label="Du", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="Au", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    group = forms.ChoiceField(label="Groupe", required=False)
    category = forms.ChoiceField(label="Catégorie", required=False)
    member = forms.ChoiceField(label="Payé par", required=False)
    min_amount = forms.DecimalField(
        label="Montant min.", required=False, min_value=0, decimal_places=3, max_digits=12
    )
    max_amount = forms.DecimalField(
        label="Montant max.", required=False, min_value=0, decimal_places=3, max_digits=12
    )
    currency = forms.ChoiceField(
        label="Devise d'affichage",
        required=False,
        choices=[(code, code) for code in SUPPORTED_CURRENCIES],
    )

    def __init__(self, *args, records=(), **kwargs):
        super().__init__(*args, **kwargs)
        groups = sorted({(str(r.group_id), r.group_name) for r in records}, key=lambda g: g[1])
        self.fields["group"].choices = [("", "Tous les groupes"), *groups]
        self.fields["category"].choices = [("", "Toutes")] + [(c, c) for c in sorted({r.category for r in records})]
        self.fields["member"].choices = [("", "Tout le monde")] + [(m, m) for m in sorted({r.payer for r in records})]
        self.fields["min_amount"].widget.attrs.update({"step": "0.001", "inputmode": "decimal"})
        self.fields["max_amount"].widget.attrs.update({"step": "0.001", "inputmode": "decimal"})

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and start > end:
            self.add_error("end", "La date de fin doit être postérieure à la date de début.")
        low, high = cleaned.get("min_amount"), cleaned.get("max_amount")
        if low is not None and high is not None and low > high:
            self.add_error("max_amount", "Le montant maximum doit être supérieur ou égal au minimum.")
        return cleaned

    def params(self) -> dict:
        return params_to_json(self.cleaned_data)


class SavedFilterForm(ExpenseFilterForm):
    name = forms.CharField(label="Nom du filtre", max_length=80)

    field_order = ["name"]

    def __init__(self, *args, user, instance=None, **kwargs):
        self.user = user
        self.instance = instance
        if instance is not None and not kwargs.get("initial"):
            kwargs["initial"] = {"name": instance.name, **instance.params}
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicates = SavedFilter.objects.filter(user=self.user, name__iexact=name)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("Vous avez déjà un filtre portant ce nom.")
        return name

    def save(self) -> SavedFilter:
        saved = self.instance or SavedFilter(user=self.user)
        saved.name = self.cleaned_data["name"]
        saved.params = self.params()
        saved.save()
        return saved


class ReportForm(AccessibleFormMixin, forms.ModelForm):
    class Meta:
        model = Report
        fields = ["title", "saved_filter", "format"]
        help_texts = {"saved_filter": "Laisser vide pour exporter toutes vos dépenses."}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["saved_filter"].queryset = SavedFilter.objects.filter(user=user)
        self.fields["saved_filter"].empty_label = "Toutes mes dépenses"
        self.fields["format"].widget = forms.RadioSelect(choices=Report.Format.choices)
