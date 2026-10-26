from decimal import Decimal

from django import forms

from accounts.forms import AccessibleFormMixin

from .models import Budget, Category
from .services import first_of_month


class CategoryForm(AccessibleFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "color", "icon"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "icon": forms.TextInput(attrs={"maxlength": 8, "autocomplete": "off"}),
        }

    def __init__(self, *args, group, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        duplicates = Category.objects.filter(group=self.group, name__iexact=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("Une catégorie porte déjà ce nom dans ce groupe.")
        return name

    def save(self, commit=True):
        self.instance.group = self.group
        return super().save(commit)


class CategoryDeleteForm(AccessibleFormMixin, forms.Form):
    reassign_to = forms.ModelChoiceField(
        label="Réaffecter les dépenses à",
        queryset=Category.objects.none(),
        required=False,
        empty_label="— Ne pas réaffecter —",
        help_text="Obligatoire si des dépenses sont rattachées à cette catégorie.",
    )

    def __init__(self, *args, category, expense_count, **kwargs):
        self.category = category
        self.expense_count = expense_count
        super().__init__(*args, **kwargs)
        self.fields["reassign_to"].queryset = Category.objects.filter(
            group=category.group
        ).exclude(pk=category.pk)

    def clean(self):
        cleaned = super().clean()
        if self.expense_count and not cleaned.get("reassign_to"):
            raise forms.ValidationError(
                f"{self.expense_count} dépense(s) utilisent cette catégorie : "
                "choisissez une catégorie de réaffectation avant de supprimer."
            )
        return cleaned


class MonthInput(forms.DateInput):
    input_type = "month"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m")


class MonthField(forms.DateField):
    widget = MonthInput

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", ["%Y-%m", "%m/%Y", "%Y-%m-%d"])
        super().__init__(**kwargs)

    def to_python(self, value):
        result = super().to_python(value)
        return first_of_month(result) if result else result


class BudgetForm(AccessibleFormMixin, forms.ModelForm):
    month = MonthField(label="Mois", help_text="Format AAAA-MM, par exemple 2026-10.")

    class Meta:
        model = Budget
        fields = ["category", "month", "limit_amount", "carry_over"]
        widgets = {
            "limit_amount": forms.NumberInput(attrs={"min": "0.001", "step": "0.001", "inputmode": "decimal"}),
        }

    def __init__(self, *args, group, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(group=group)
        self.fields["category"].empty_label = "— Choisir une catégorie —"
        self.fields["limit_amount"].label = f"Limite mensuelle ({group.currency})"

    def clean_limit_amount(self):
        limit = self.cleaned_data["limit_amount"]
        if limit is not None and limit <= 0:
            raise forms.ValidationError("La limite doit être strictement positive.")
        return limit

    def clean(self):
        cleaned = super().clean()
        category, month = cleaned.get("category"), cleaned.get("month")
        if category and month:
            duplicates = Budget.objects.filter(category=category, month=month)
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                self.add_error(
                    "month",
                    f"Un budget existe déjà pour « {category.name} » en {month:%m/%Y}.",
                )
        return cleaned

    def save(self, commit=True):
        self.instance.group = self.group
        return super().save(commit)


class SimulationForm(AccessibleFormMixin, forms.Form):
    category = forms.ModelChoiceField(
        label="Catégorie",
        queryset=Category.objects.none(),
        empty_label="— Choisir une catégorie —",
    )
    reduction_percent = forms.DecimalField(
        label="Réduction (%)",
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        decimal_places=1,
        initial=Decimal("20"),
        widget=forms.NumberInput(attrs={"step": "1", "inputmode": "decimal"}),
        help_text="Entre 0 et 100.",
    )
    savings_goal = forms.DecimalField(
        label="Objectif d'épargne",
        min_value=Decimal("0.001"),
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"step": "any", "min": "0.001", "inputmode": "decimal"}),
    )

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(group=group)
        self.fields["savings_goal"].label = f"Objectif d'épargne ({group.currency})"


class SavingsPlanForm(AccessibleFormMixin, forms.Form):
    goal = forms.DecimalField(
        label="Objectif d'épargne",
        min_value=Decimal("1"),
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"step": "any", "min": "1", "inputmode": "decimal"}),
    )
    months = forms.IntegerField(
        label="En combien de mois ?",
        min_value=1,
        max_value=24,
        initial=3,
        widget=forms.NumberInput(attrs={"min": "1", "max": "24"}),
        help_text="Entre 1 et 24 mois.",
    )

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["goal"].label = f"Objectif d'épargne ({group.currency})"
