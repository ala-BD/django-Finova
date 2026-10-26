from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from accounts.forms import AccessibleFormMixin

from .models import Group, GroupCharter, Invitation, Membership
from .services import default_weight_for


class GroupForm(AccessibleFormMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = ("name", "description", "currency", "default_split_rule")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "default_split_rule": (
                "Égale : même part. Proportionnelle au revenu : selon le poids "
                "(1 / 2 / 3). Proportionnelle à la consommation : selon le ticket (M2)."
            ),
        }

    def __init__(self, *args, currency_locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        if currency_locked:
            self.fields["currency"].disabled = True
            self.fields["currency"].help_text = (
                "La devise ne peut plus être modifiée : des dépenses existent déjà."
            )

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if len(name) < 3:
            raise ValidationError("Le nom doit contenir entre 3 et 80 caractères.")
        return name


class MembershipForm(AccessibleFormMixin, forms.ModelForm):
    use_profile_weight = forms.BooleanField(
        required=False,
        label="Appliquer le poids du profil de revenu",
        help_text="Étudiant = 1, actif = 2, actif senior = 3.",
    )

    class Meta:
        model = Membership
        fields = ("role", "weight")
        widgets = {
            "weight": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
        }
        help_texts = {
            "weight": "Doit être > 0. Exemple : 90 TND avec poids 1, 2 et 3 → 15 / 30 / 45.",
        }

    def clean_weight(self):
        weight = self.cleaned_data["weight"]
        if weight is None or weight <= 0:
            raise ValidationError("Le poids doit être strictement positif.")
        return weight

    def clean(self):
        cleaned = super().clean()
        membership = self.instance
        role = cleaned.get("role")
        if cleaned.get("use_profile_weight") and membership.pk:
            cleaned["weight"] = default_weight_for(membership.user)
        if (
            membership.pk
            and membership.role == Membership.Role.ADMIN
            and role == Membership.Role.MEMBER
            and membership.group.admin_count() <= 1
        ):
            self.add_error(
                "role",
                "Le groupe doit toujours conserver au moins un admin.",
            )
        return cleaned


class SplitPreviewForm(AccessibleFormMixin, forms.Form):
    amount = forms.DecimalField(
        label="Montant à répartir",
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        initial=Decimal("90.000"),
        widget=forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
    )


class InvitationForm(AccessibleFormMixin, forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ("email",)
        widgets = {
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
        }

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise ValidationError("Indiquez une adresse e-mail valide.")
        if self.group is None:
            return email
        if self.group.memberships.filter(
            user__email__iexact=email,
            is_active=True,
        ).exists():
            raise ValidationError("Cette personne est déjà membre du groupe.")
        if self.group.invitations.filter(
            email=email,
            status=Invitation.Status.PENDING,
        ).exists():
            raise ValidationError("Une invitation active existe déjà pour cet e-mail.")
        return email


class CharterForm(AccessibleFormMixin, forms.ModelForm):
    class Meta:
        model = GroupCharter
        fields = ("group_type", "key_rules", "content")
        widgets = {
            "key_rules": forms.Textarea(attrs={
                "rows": 2,
                "placeholder": "ex. dépenses communes réglées à la fin du mois ; pas d'achat de plus de 50 TND sans accord",
            }),
            "content": forms.Textarea(attrs={"rows": 14}),
        }
        help_texts = {
            "content": "Format simple : une règle par ligne, numérotée (1. …). Le gras s'écrit **ainsi**.",
        }

    def clean_content(self):
        content = (self.cleaned_data.get("content") or "").strip()
        if not content:
            raise ValidationError("La charte ne peut pas être vide.")
        return content
