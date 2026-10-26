from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class AccessibleFormMixin:
    """Ajoute les attributs ARIA une fois le formulaire validé."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label_suffix = ""
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            classes = field.widget.attrs.get("class", "")
            if "input" not in classes.split():
                field.widget.attrs["class"] = f"{classes} input".strip()

    def full_clean(self):
        super().full_clean()
        for name, field in self.fields.items():
            bound = self[name]
            described_by = []
            if field.help_text:
                described_by.append(f"{bound.id_for_label}-help")
            if bound.errors:
                field.widget.attrs["aria-invalid"] = "true"
                described_by.append(f"{bound.id_for_label}-error")
            else:
                field.widget.attrs.pop("aria-invalid", None)
            if described_by:
                field.widget.attrs["aria-describedby"] = " ".join(described_by)
            else:
                field.widget.attrs.pop("aria-describedby", None)


class LoginForm(AccessibleFormMixin, AuthenticationForm):
    """Connexion : message d'échec générique, sans fuite sur l'existence du compte."""

    error_messages = {
        "invalid_login": _(
            "Identifiant ou mot de passe incorrect."
        ),
        "inactive": _("Identifiant ou mot de passe incorrect."),
    }

    username = forms.CharField(
        label=_("Identifiant"),
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "autocapitalize": "none",
                "spellcheck": "false",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label=_("Mot de passe"),
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password"}
        ),
    )


class RegisterForm(AccessibleFormMixin, UserCreationForm):
    first_name = forms.CharField(
        label=_("Prénom"),
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        label=_("Nom"),
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
    username = forms.CharField(
        label=_("Identifiant"),
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    email = forms.EmailField(
        label=_("Adresse e-mail"),
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label=_("Mot de passe"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label=_("Confirmer le mot de passe"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "username", "email")

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("Cette adresse e-mail est déjà utilisée."))
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user

