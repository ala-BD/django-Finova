from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _


class AccessibleFormMixin:
    """Ajoute les attributs ARIA une fois le formulaire validé."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label_suffix = ""
        for field in self.fields.values():
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
