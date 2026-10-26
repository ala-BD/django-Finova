from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core.permissions import GroupAdminRequiredMixin, GroupMemberRequiredMixin

from .forms import GroupForm, InvitationForm, MembershipForm
from .models import Group, Invitation, Membership
from .services import can_leave, default_weight_for, get_member_balance, is_admin


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
        context.update(
            {
                "members": members,
                "pending_invitations": group.invitations.filter(
                    status=Invitation.Status.PENDING,
                    expires_at__gt=timezone.now(),
                ),
                "is_group_admin": is_admin(self.request.user, group),
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
        context["group"] = self.get_group()
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
        messages.success(
            self.request,
            f"Invitation envoyée à {invitation.email}. Lien (7 jours) : {accept_url}",
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
