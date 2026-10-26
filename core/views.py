import logging

from django.http import HttpResponseServerError
from django.shortcuts import render
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def home(request):
    return render(request, "home.html")


# Pages d'erreur personnalisées (Imem). Branchées dans config/urls.py (handler400…500)
# et settings.CSRF_FAILURE_VIEW. Actives quand DEBUG=False.

def error_400(request, exception=None):
    return render(request, "400.html", status=400)


def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", {"path": request.path}, status=404)


def error_500(request):
    # Rendu sans contexte de requête : la page doit s'afficher même si la base
    # de données ou un context processor est à l'origine de l'erreur.
    return HttpResponseServerError(render_to_string("500.html"))


def csrf_failure(request, reason=""):
    logger.warning("Échec CSRF sur %s : %s", request.path, reason)
    return render(request, "403_csrf.html", status=403)
