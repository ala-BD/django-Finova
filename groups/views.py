from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core.markdown import render_markdown
from core.permissions import GroupAdminRequiredMixin, GroupMemberRequiredMixin

from decimal import Decimal

from .emails import send_invitation_email
from .forms import CharterForm, GroupForm, InvitationForm, MembershipForm, SplitPreviewForm
from .models import Group, GroupCharter, Invitation, Membership
from .services import (
    can_leave,
    coach_answer,
    default_weight_for,
    generate_charter,
    generate_group_from_prompt,
    get_member_balance,
    is_admin,
    parse_coach_members,
    preview_split,
)


class GroupListView(LoginRequiredMixin, ListView):
    template_name = "groups/list.html"
    context_object_name = "groups"

    def get_queryset(self):
        return (
            Group.objects.filter(
                memberships__user=self.request.user,
                memberships__is_active=True,
                is_archived=False,
            )
            .distinct()
            .order_by("name")
        )


class GroupCreateView(LoginRequiredMixin, CreateView):
    model = Group
    form_class = GroupForm
    template_name = "groups/form.html"

    def form_valid(self, form):
        with transaction.atomic():
            group = form.save(commit=False)
            group.created_by = self.request.user
            group.save()
            Membership.objects.create(
                group=group,
                user=self.request.user,
                role=Membership.Role.ADMIN,
                weight=default_weight_for(self.request.user),
            )
            # Catégories / budgets proposés par l'IA et conservés par l'utilisateur (champ caché)
            suggestions = _parse_ai_suggestions(self.request.POST.get("ai_suggestions", ""))
            if suggestions["categories"]:
                _create_budgets_if_available(group, suggestions, self.request.user)
        messages.success(self.request, "Groupe créé. Vous en êtes l'admin.")
        return redirect("groups:detail", gid=group.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Nouveau groupe"
        context["submit_label"] = "Créer le groupe"
        return context


class GroupDetailView(GroupMemberRequiredMixin, DetailView):
    model = Group
    pk_url_kwarg = "gid"
    template_name = "groups/detail.html"
    context_object_name = "group"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.object
        members = list(group.active_memberships())
        for membership in members:
            membership.balance = get_member_balance(membership.user, group)
            membership.suggested_weight = default_weight_for(membership.user)
        if self.request.GET.get("amount"):
            preview_form = SplitPreviewForm(self.request.GET)
            amount = (
                preview_form.cleaned_data["amount"]
                if preview_form.is_valid()
                else Decimal("90.000")
            )
        else:
            preview_form = SplitPreviewForm(initial={"amount": Decimal("90.000")})
            amount = Decimal("90.000")
        context.update(
            {
                "members": members,
                "pending_invitations": group.invitations.filter(
                    status=Invitation.Status.PENDING,
                    expires_at__gt=timezone.now(),
                ),
                "is_group_admin": is_admin(self.request.user, group),
                "preview_form": preview_form,
                "preview": preview_split(group, amount),
            }
        )
        return context


class GroupUpdateView(GroupAdminRequiredMixin, UpdateView):
    model = Group
    form_class = GroupForm
    pk_url_kwarg = "gid"
    template_name = "groups/form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["currency_locked"] = self.object.has_expenses()
        return kwargs

    def get_success_url(self):
        messages.success(self.request, "Groupe mis à jour.")
        return reverse("groups:detail", kwargs={"gid": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Modifier {self.object.name}"
        context["submit_label"] = "Enregistrer"
        context["group"] = self.object
        return context


class GroupArchiveView(GroupAdminRequiredMixin, View):
    template_name = "groups/archive_confirm.html"

    def get(self, request, gid):
        return render(request, self.template_name, {"group": self.get_group()})

    def post(self, request, gid):
        group = self.get_group()
        group.is_archived = True
        group.save(update_fields=["is_archived", "updated_at"])
        messages.success(request, f"Le groupe « {group.name} » a été archivé.")
        return redirect("groups:list")


class GroupLeaveView(GroupMemberRequiredMixin, View):
    template_name = "groups/leave_confirm.html"

    def get(self, request, gid):
        group = self.get_group()
        allowed, reason = can_leave(request.user, group)
        return render(
            request,
            self.template_name,
            {"group": group, "allowed": allowed, "reason": reason},
        )

    def post(self, request, gid):
        group = self.get_group()
        allowed, reason = can_leave(request.user, group)
        if not allowed:
            messages.error(request, reason)
            return redirect("groups:detail", gid=group.pk)
        membership = group.memberships.get(user=request.user, is_active=True)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        messages.success(request, f"Vous avez quitté le groupe « {group.name} ».")
        return redirect("groups:list")


class MembershipUpdateView(GroupAdminRequiredMixin, UpdateView):
    model = Membership
    form_class = MembershipForm
    pk_url_kwarg = "mid"
    template_name = "groups/member_form.html"

    def get_queryset(self):
        return Membership.objects.filter(
            group_id=self.kwargs["gid"],
            is_active=True,
        )

    def get_success_url(self):
        messages.success(self.request, "Membre mis à jour.")
        return reverse("groups:detail", kwargs={"gid": self.object.group_id})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = self.object
        context["group"] = self.get_group()
        context["suggested_weight"] = default_weight_for(membership.user)
        context["income_profile"] = membership.user.get_income_profile_display()
        return context


class InvitationCreateView(GroupAdminRequiredMixin, CreateView):
    model = Invitation
    form_class = InvitationForm
    template_name = "groups/invite_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["group"] = self.get_group()
        return kwargs

    def form_valid(self, form):
        invitation = form.save(commit=False)
        invitation.group = self.get_group()
        invitation.invited_by = self.request.user
        invitation.save()
        accept_url = self.request.build_absolute_uri(
            reverse("invitations:accept", kwargs={"token": invitation.token})
        )
        if send_invitation_email(invitation, accept_url, self.request.user):
            messages.success(
                self.request,
                f"Invitation envoyée par e-mail à {invitation.email} (valable 7 jours).",
            )
        else:
            messages.warning(
                self.request,
                f"L'invitation est créée mais l'e-mail n'a pas pu être envoyé à {invitation.email}. "
                f"Transmettez-lui ce lien (valable 7 jours) : {accept_url}",
            )
        return redirect("groups:detail", gid=invitation.group_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.get_group()
        return context


class InvitationAcceptView(LoginRequiredMixin, View):
    template_name = "groups/invite_accept.html"

    def get_invitation(self, token):
        invitation = get_object_or_404(Invitation, token=token)
        invitation.refresh_expiration_status()
        return invitation

    def get(self, request, token):
        invitation = self.get_invitation(token)
        error = self._refusal_reason(request.user, invitation)
        return render(
            request,
            self.template_name,
            {"invitation": invitation, "error": error},
        )

    def post(self, request, token):
        invitation = self.get_invitation(token)
        error = self._refusal_reason(request.user, invitation)
        if error:
            messages.error(request, error)
            return render(
                request,
                self.template_name,
                {"invitation": invitation, "error": error},
            )
        with transaction.atomic():
            membership, created = Membership.objects.get_or_create(
                group=invitation.group,
                user=request.user,
                defaults={
                    "role": Membership.Role.MEMBER,
                    "weight": default_weight_for(request.user),
                    "is_active": True,
                },
            )
            if not created:
                membership.is_active = True
                membership.save(update_fields=["is_active"])
            invitation.status = Invitation.Status.ACCEPTED
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["status", "accepted_at"])
        messages.success(request, f"Vous avez rejoint « {invitation.group.name} ».")
        return redirect("groups:detail", gid=invitation.group_id)

    def _refusal_reason(self, user, invitation) -> str:
        if invitation.status == Invitation.Status.EXPIRED or invitation.is_expired():
            return "Cette invitation a expiré."
        if invitation.status == Invitation.Status.ACCEPTED:
            return "Cette invitation a déjà été utilisée."
        if invitation.status != Invitation.Status.PENDING:
            return "Cette invitation n'est plus valable."
        if user.email.lower() != invitation.email:
            return (
                "Connectez-vous avec le compte correspondant à "
                f"{invitation.email} pour accepter l'invitation."
            )
        if invitation.group.memberships.filter(user=user, is_active=True).exists():
            return "Vous êtes déjà membre de ce groupe."
        if invitation.group.is_archived:
            return "Ce groupe a été archivé."
        return ""


# ---------------------------------------------------------------------------
# Vue IA — création de groupe par description en langage naturel
# ---------------------------------------------------------------------------

class GroupAICreateView(LoginRequiredMixin, View):
    """
    GET  → affiche la page avec le formulaire IA + le formulaire manuel
    POST → endpoint fetch JSON asynchrone : génère le groupe via LLM
              et le crée en base dans une transaction atomique.

    Réponse JSON :
      succès  → {"ok": true,  "redirect": "/groups/<id>/"}
      erreur  → {"ok": false, "error": "message", "retry": true/false}
    """
    template_name = "groups/ai_create.html"

    def get(self, request):
        return render(request, self.template_name, {"form": GroupForm()})

    def post(self, request):
        import json
        from core.ai import LLMError

        prompt = (request.POST.get("prompt") or "").strip()
        if not prompt:
            return JsonResponse(
                {"ok": False, "error": "Décrivez votre projet en une phrase.", "retry": False},
                status=400,
            )

        try:
            group_data = generate_group_from_prompt(prompt, user=request.user)
        except LLMError as exc:
            return JsonResponse(
                {"ok": False, "error": str(exc), "retry": False},
                status=503,
            )
        except ValueError as exc:
            return JsonResponse(
                {"ok": False, "error": str(exc), "retry": True},
                status=422,
            )

        # Rien n'est créé ici : la suggestion pré-remplit le formulaire, l'utilisateur la vérifie
        # puis la soumet à GroupCreateView (catégories et budgets transmis en champ caché).
        return JsonResponse(
            {
                "ok": True,
                "fields": {
                    "name": group_data["name"],
                    "description": group_data["description"],
                    "currency": group_data["currency"],
                    "default_split_rule": group_data["default_split_rule"],
                },
                "categories": group_data["categories"],
                "budgets": group_data["budgets"],
            }
        )


def _create_budgets_if_available(group, group_data: dict, user) -> None:
    """
    Crée les catégories et budgets générés par l'IA (contrat M4).
    Si le module budgets n'est pas encore implémenté, ignore silencieusement.
    """
    from django.apps import apps
    try:
        Category = apps.get_model("budgets", "Category")
    except LookupError:
        return  # Module M4 pas encore prêt
    try:
        Budget = apps.get_model("budgets", "Budget")
    except LookupError:
        Budget = None  # les catégories sont créées même si les budgets ne sont pas encore livrés

    budget_map = {b["category"]: b["amount"] for b in group_data.get("budgets", [])}
    for cat_name in group_data.get("categories", []):
        try:
            # Point de sauvegarde : une erreur ici ne doit pas invalider la transaction du groupe
            with transaction.atomic():
                cat = Category.objects.filter(group=group, name__iexact=cat_name).first()
                if cat is None:
                    cat = Category.objects.create(group=group, name=cat_name)
                amount = budget_map.get(cat_name)
                if Budget is not None and amount:
                    Budget.objects.get_or_create(
                        group=group,
                        category=cat,
                        defaults={"amount": amount},
                    )
        except Exception:
            pass  # Ne jamais bloquer la création du groupe à cause de M4


def _parse_ai_suggestions(raw: str) -> dict:
    """Catégories / budgets renvoyés par le formulaire (champ caché) : on ne fait pas confiance au
    navigateur, tout est revalidé (types, longueurs, montants positifs, 10 catégories au plus)."""
    import json

    empty = {"categories": [], "budgets": []}
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    categories = []
    for name in data.get("categories", []) if isinstance(data.get("categories"), list) else []:
        name = str(name).strip()[:60]
        if name and name.casefold() not in {c.casefold() for c in categories}:
            categories.append(name)
    categories = categories[:10]
    budgets = []
    for item in data.get("budgets", []) if isinstance(data.get("budgets"), list) else []:
        if isinstance(item, dict) and str(item.get("category", "")).strip() in categories:
            try:
                amount = float(item.get("amount"))
            except (TypeError, ValueError):
                continue
            if 0 < amount <= 10_000_000:
                budgets.append({"category": str(item["category"]).strip(), "amount": amount})
    return {"categories": categories, "budgets": budgets}


# ---------------------------------------------------------------------------
# Charte du groupe (IA) : lue par tous les membres, rédigée et modifiée par les admins
# ---------------------------------------------------------------------------

class CharterView(GroupMemberRequiredMixin, View):
    template_name = "groups/charter.html"

    def get(self, request, gid):
        group = self.get_group()
        return render(request, self.template_name, {
            "group": group,
            "charter": GroupCharter.objects.filter(group=group).select_related("updated_by").first(),
            "is_group_admin": is_admin(request.user, group),
        })


class CharterEditView(GroupAdminRequiredMixin, View):
    template_name = "groups/charter_form.html"

    def get_charter(self):
        return GroupCharter.objects.filter(group=self.get_group()).first()

    def render_form(self, form):
        return render(self.request, self.template_name, {"group": self.get_group(), "form": form})

    def get(self, request, gid):
        charter = self.get_charter()
        initial = {} if charter else {"group_type": guess_group_type(self.get_group())}
        return self.render_form(CharterForm(instance=charter, initial=initial))

    def post(self, request, gid):
        form = CharterForm(request.POST, instance=self.get_charter())
        if not form.is_valid():
            return self.render_form(form)
        charter = form.save(commit=False)
        charter.group = self.get_group()
        charter.updated_by = request.user
        charter.generated_by_ai = request.POST.get("ai_generated") == "1"
        charter.save()
        messages.success(request, "Charte enregistrée : tous les membres peuvent la consulter.")
        return redirect("groups:charter", gid=charter.group_id)


class CharterGenerateView(GroupAdminRequiredMixin, View):
    """Appelé en fetch : propose une charte (LLM, ou modèle de texte en repli). Ne sauvegarde rien."""

    http_method_names = ["post"]

    def post(self, request, gid):
        group_type = request.POST.get("group_type", "autre")
        if group_type not in GroupCharter.GroupType.values:
            return JsonResponse({"error": "Type de groupe inconnu."}, status=400)
        key_rules = (request.POST.get("key_rules") or "").strip()[:500]
        result = generate_charter(self.get_group(), group_type, key_rules, user=request.user)
        return JsonResponse(result)


def guess_group_type(group) -> str:
    text = f"{group.name} {group.description}".casefold()
    for group_type, words in {
        "coloc": ("coloc", "appart", "loyer", "maison"),
        "voyage": ("voyage", "vacances", "trip", "séjour", "sejour", "week-end"),
        "evenement": ("anniversaire", "mariage", "fête", "fete", "soirée", "soiree", "événement", "evenement"),
        "association": ("asso", "club", "équipe", "equipe"),
        "famille": ("famille", "parents", "cousins"),
    }.items():
        if any(word in text for word in words):
            return group_type
    return "autre"


# ---------------------------------------------------------------------------
# Coach d'équité interactif : compare les règles, recommande, l'IA explique
# ---------------------------------------------------------------------------

class EquityCoachView(GroupMemberRequiredMixin, View):
    template_name = "groups/equity_coach.html"

    def get(self, request, gid):
        group = self.get_group()
        members = [
            {"name": m.user.get_full_name() or m.user.username, "weight": float(m.weight)}
            for m in group.active_memberships()
        ]
        return render(request, self.template_name, {
            "group": group,
            "is_group_admin": is_admin(request.user, group),
            "coach_config": {
                "members": members,
                "amount": 90,
                "currency": group.currency,
                "current_rule": group.default_split_rule,
                "profiles": [
                    {"label": "Étudiant·e", "weight": 1},
                    {"label": "Actif·ve", "weight": 2},
                    {"label": "Actif·ve senior", "weight": 3},
                ],
            },
        })


class EquityCoachAskView(GroupMemberRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, gid):
        try:
            amount = Decimal(str(request.POST.get("amount", "")).replace(",", "."))
        except Exception:
            amount = Decimal("0")
        if not Decimal("0.001") <= amount <= Decimal("10000000"):
            return JsonResponse({"error": "Le montant doit être compris entre 0,001 et 10 000 000."}, status=400)
        try:
            members = parse_coach_members(request.POST.get("members", ""))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        question = (request.POST.get("question") or "").strip()[:500]
        result = coach_answer(self.get_group(), amount, members, question, user=request.user)
        result["explanation_html"] = render_markdown(result["explanation"])
        return JsonResponse(result)


class ApplySplitRuleView(GroupAdminRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, gid):
        group = self.get_group()
        rule = request.POST.get("rule")
        if rule not in Group.SplitRule.values:
            messages.error(request, "Règle de partage inconnue.")
        else:
            group.default_split_rule = rule
            group.save(update_fields=["default_split_rule", "updated_at"])
            messages.success(request, f"Règle de partage du groupe : « {group.get_default_split_rule_display().lower()} ».")
        return redirect("groups:equity", gid=group.pk)
