import logging

from django.http import HttpResponseServerError
from django.shortcuts import render
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def home(request):
    """Accueil : présentation pour les visiteurs, carnet des groupes (avec mon solde) pour les membres."""
    if not request.user.is_authenticated:
        return render(request, "home.html")

    from groups.models import Membership
    from settlements.services import compute_balances

    memberships = (
        Membership.objects.filter(user=request.user, is_active=True, group__is_archived=False)
        .select_related("group")
        .order_by("group__name")
    )
    tiles = []
    for membership in memberships:
        group = membership.group
        balance = compute_balances(group).get(request.user.pk)
        tiles.append({
            "group": group,
            "members": group.memberships.filter(is_active=True).count(),
            "is_admin": membership.is_admin,
            "balance": balance,
            "magnitude": abs(balance) if balance is not None else None,
        })
    # Totaux par devise : on n'additionne jamais des TND et des EUR
    totals = {}
    for tile in tiles:
        row = totals.setdefault(tile["group"].currency, {"currency": tile["group"].currency, "owed": 0, "owe": 0})
        if tile["balance"] and tile["balance"] > 0:
            row["owed"] += tile["balance"]
        elif tile["balance"] and tile["balance"] < 0:
            row["owe"] -= tile["balance"]
    rows = list(totals.values())
    return render(request, "home.html", {
        "tiles": tiles,
        "totals": rows,
        # Une ligne « 0 » n'est affichée que si aucune devise n'a de montant
        "totals_owed": any(row["owed"] for row in rows),
        "totals_owe": any(row["owe"] for row in rows),
    })


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
