from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator

from .forms import LoginForm


class LoginView(SuccessMessageMixin, DjangoLoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True
    success_message = "Connexion réussie."


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(View):
    """Déconnexion uniquement en POST, protégée par CSRF."""

    http_method_names = ["post"]
    next_page = reverse_lazy("accounts:login")

    def post(self, request):
        auth_logout(request)
        messages.success(request, "Vous êtes déconnecté.")
        return redirect(self.next_page)


def register_placeholder(request):
    """Yahya : formulaire d'inscription."""
    return HttpResponse("Inscription — à implémenter (Yahya).")
