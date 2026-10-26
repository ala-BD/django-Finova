import logging
import smtplib
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core.permissions import GroupAdminRequiredMixin, GroupMemberRequiredMixin

from .forms import GroupForm, InvitationForm, MembershipForm, SplitPreviewForm
from .models import Group, Invitation, Membership
from .services import (
    can_leave,
    compare_equity_rules,
    default_weight_for,
    generate_group_from_prompt,
    generate_invitation_message,
    get_member_balance,
    is_admin,
    preview_split,
    validate_group_proposal,
)

logger = logging.getLogger(__name__)


class GroupListView(LoginRequiredMixin, ListView):
    template_name = "groups/list.html"
    context_object_name = "groups"

    def get_queryset(self):
        queryset = (
            Group.objects.filter(
                memberships__user=self.request.user,
                memberships__is_active=True,
                is_archived=False,
            )
            .distinct()
        )
        search_query = self.request.GET.get("q", "").strip()
        currency = self.request.GET.get("currency", "")
        split_rule = self.request.GET.get("split_rule", "")

        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query)
                | Q(description__icontains=search_query)
            )
        if currency in Group.Currency.values:
            queryset = queryset.filter(currency=currency)
        if split_rule in Group.SplitRule.values:
            queryset = queryset.filter(default_split_rule=split_rule)

        return queryset.order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "search_query": self.request.GET.get("q", "").strip(),
                "selected_currency": self.request.GET.get("currency", ""),
                "selected_split_rule": self.request.GET.get("split_rule", ""),
                "currency_choices": Group.Currency.choices,
                "split_rule_choices": Group.SplitRule.choices,
            }
        )
        return context


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

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") != "generate_message":
            return super().post(request, *args, **kwargs)

        from core.ai import LLMError

        form = self.get_form()
        if not form.is_valid():
            return JsonResponse(
                {
                    "ok": False,
                    "error": " ".join(
                        error
                        for errors in form.errors.values()
                        for error in errors
                    ),
                },
                status=400,
            )
        try:
            message = generate_invitation_message(
                self.get_group(),
                user=request.user,
            )
        except LLMError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=503)

        return JsonResponse({"ok": True, "message": message})

    def form_valid(self, form):
        invitation = form.save(commit=False)
        invitation.group = self.get_group()
        invitation.invited_by = self.request.user
        invitation.save()
        accept_path = reverse("invitations:accept", kwargs={"token": invitation.token})
        if settings.PUBLIC_BASE_URL:
            accept_url = f"{settings.PUBLIC_BASE_URL}{accept_path}"
        else:
            accept_url = self.request.build_absolute_uri(accept_path)

        if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
            messages.warning(
                self.request,
                "Invitation créée, mais aucun e-mail n'a été envoyé : le backend "
                "console affiche le message dans le terminal du serveur. "
                f"Lien valable 7 jours : {accept_url}",
            )
            return redirect("groups:detail", gid=invitation.group_id)

        email_body = (
            "Bonjour,\n\n"
            f"Vous êtes invité(e) à rejoindre le groupe « {invitation.group.name} » "
            "sur Finova.\n"
        )
        if invitation.group.description:
            email_body += f"\nÀ propos du groupe : {invitation.group.description}\n"
        email_body += (
            f"\nRègle de partage : "
            f"{invitation.group.get_default_split_rule_display()}.\n\n"
            f"Pour rejoindre le groupe, ouvrez ce lien :\n{accept_url}\n\n"
            "Ce lien personnel expire dans 7 jours.\n"
        )
        try:
            sent_count = send_mail(
                subject=f"Invitation Finova — {invitation.group.name}",
                message=email_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[invitation.email],
                fail_silently=False,
            )
        except (smtplib.SMTPException, OSError):
            logger.exception(
                "Unable to send group invitation email for invitation %s",
                invitation.pk,
            )
            messages.error(
                self.request,
                "L'invitation a été créée, mais l'e-mail n'a pas pu être envoyé. "
                f"Vérifiez la configuration SMTP. Lien valable 7 jours : {accept_url}",
            )
        else:
            if sent_count != 1:
                logger.error(
                    "Email backend did not send invitation email for invitation %s",
                    invitation.pk,
                )
                messages.error(
                    self.request,
                    "L'invitation a été créée, mais le serveur e-mail ne l'a pas "
                    "confirmée comme envoyée. Vérifiez la configuration SMTP. "
                    f"Lien valable 7 jours : {accept_url}",
                )
            else:
                messages.success(
                    self.request,
                    f"Invitation envoyée par e-mail à {invitation.email}. "
                    "Le lien expire dans 7 jours.",
                )
        return redirect("groups:detail", gid=invitation.group_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.get_group()
        return context


class EquityCoachView(GroupMemberRequiredMixin, View):
    def post(self, request, gid):
        from core.ai import LLMError

        form = SplitPreviewForm(request.POST)
        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "error": "Saisissez un montant valide."},
                status=400,
            )
        try:
            comparison = compare_equity_rules(
                self.get_group(),
                form.cleaned_data["amount"],
                user=request.user,
            )
        except LLMError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=503)
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

        return JsonResponse({"ok": True, "comparison": comparison})


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
        from core.ai import LLMError

        action = request.POST.get("action", "generate")
        if action == "confirm":
            budget_categories = request.POST.getlist("budget_category")
            budget_amounts = request.POST.getlist("budget_amount")
            if len(budget_categories) != len(budget_amounts):
                return JsonResponse(
                    {"ok": False, "error": "Les lignes de budget sont invalides.", "retry": False},
                    status=422,
                )

            proposal = {
                "name": request.POST.get("name", ""),
                "description": request.POST.get("description", ""),
                "currency": request.POST.get("currency", ""),
                "default_split_rule": request.POST.get("default_split_rule", ""),
                "categories": request.POST.getlist("categories"),
                "budgets": [
                    {"category": category, "amount": amount}
                    for category, amount in zip(budget_categories, budget_amounts)
                ],
            }
            try:
                group_data = validate_group_proposal(proposal)
            except ValueError as exc:
                return JsonResponse(
                    {"ok": False, "error": str(exc), "retry": False},
                    status=422,
                )

            with transaction.atomic():
                group = Group.objects.create(
                    name=group_data["name"],
                    description=group_data["description"],
                    currency=group_data["currency"],
                    default_split_rule=group_data["default_split_rule"],
                    created_by=request.user,
                )
                Membership.objects.create(
                    group=group,
                    user=request.user,
                    role=Membership.Role.ADMIN,
                    weight=default_weight_for(request.user),
                )
                _create_budgets_if_available(group, group_data, request.user)

            return JsonResponse(
                {
                    "ok": True,
                    "redirect": reverse("groups:detail", kwargs={"gid": group.pk}),
                    "group_name": group.name,
                }
            )

        if action != "generate":
            return JsonResponse(
                {"ok": False, "error": "Action inconnue.", "retry": False},
                status=400,
            )

        prompt = (request.POST.get("prompt") or "").strip()
        if not prompt:
            return JsonResponse(
                {"ok": False, "error": "Décrivez votre projet en une phrase.", "retry": False},
                status=400,
            )
        if len(prompt) > 300:
            return JsonResponse(
                {"ok": False, "error": "La description ne peut pas dépasser 300 caractères.", "retry": False},
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

        return JsonResponse(
            {
                "ok": True,
                "proposal": group_data,
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
        Budget = apps.get_model("budgets", "Budget")
    except LookupError:
        return  # Module M4 pas encore prêt

    budget_map = {b["category"]: b["amount"] for b in group_data.get("budgets", [])}
    for cat_name in group_data.get("categories", []):
        try:
            cat, _ = Category.objects.get_or_create(name=cat_name, group=group)
            amount = budget_map.get(cat_name)
            if amount:
                Budget.objects.get_or_create(
                    group=group,
                    category=cat,
                    defaults={"amount": amount},
                )
        except Exception:
            pass  # Ne jamais bloquer la création du groupe à cause de M4
