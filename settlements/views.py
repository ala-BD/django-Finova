from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, TemplateView

from core.permissions import GroupMemberRequiredMixin
from groups.services import is_admin

from . import exports, services
from .forms import (
    InstallmentPaymentForm,
    ReminderForm,
    ReminderSendForm,
    RepaymentPlanForm,
    SettlementForm,
)
from .models import Installment, RepaymentPlan, Settlement


def validation_message(exc):
    return " ".join(exc.messages)


class GroupSettlementMixin(GroupMemberRequiredMixin):
    """Accès réservé aux membres ; tout objet est cherché DANS le groupe (sinon 404)."""

    def get_membership(self):
        return self.get_group().memberships.get(user=self.request.user, is_active=True)

    def get_settlement(self):
        return get_object_or_404(
            Settlement.objects.select_related("payer__user", "receiver__user", "group"),
            pk=self.kwargs["sid"],
            group=self.get_group(),
        )

    def detail_url(self, settlement):
        return reverse("settlements:detail", kwargs={"gid": settlement.group_id, "sid": settlement.pk})


class BalancesView(GroupSettlementMixin, TemplateView):
    template_name = "settlements/balances.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.get_group()
        overview = services.balance_overview(group)
        context.update(overview)
        context["group"] = group
        context["chart_data"] = {
            "labels": [
                row["membership"].user.get_full_name() or row["membership"].user.username
                for row in overview["rows"]
            ],
            "values": [float(row["balance"]) for row in overview["rows"]],
            "currency": group.currency,
        }
        context["me"] = self.get_membership()
        return context


class SettlementListView(GroupSettlementMixin, ListView):
    template_name = "settlements/list.html"
    context_object_name = "settlements"
    paginate_by = 20

    def get_queryset(self):
        queryset = Settlement.objects.filter(group=self.get_group()).select_related(
            "payer__user", "receiver__user"
        )
        status = self.request.GET.get("status")
        if status in Settlement.Status.values:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.get_group()
        context["statuses"] = Settlement.Status.choices
        context["current_status"] = self.request.GET.get("status", "")
        context["to_confirm"] = Settlement.objects.filter(
            group=self.get_group(),
            status=Settlement.Status.PENDING,
            receiver__user=self.request.user,
        ).count()
        return context


class SettlementCreateView(GroupSettlementMixin, View):
    template_name = "settlements/form.html"

    def get_form(self, data=None):
        initial = {}
        if data is None:
            # Pré-remplissage depuis un virement simplifié proposé (?receiver=<mid>&amount=…)
            initial["receiver"] = self.request.GET.get("receiver")
            try:
                initial["amount"] = Decimal(self.request.GET.get("amount", ""))
            except InvalidOperation:
                pass
        return SettlementForm(data, initial=initial, group=self.get_group(), payer=self.get_membership())

    def render_form(self, form):
        return render(self.request, self.template_name, {"form": form, "group": self.get_group()})

    def get(self, request, gid):
        return self.render_form(self.get_form())

    def post(self, request, gid):
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_form(form)
        try:
            settlement = services.create_settlement(
                self.get_group(),
                payer=self.get_membership(),
                receiver=form.cleaned_data["receiver"],
                amount=form.cleaned_data["amount"],
                note=form.cleaned_data["note"],
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
            return self.render_form(form)
        messages.success(
            request,
            "Remboursement enregistré. Il sera pris en compte dans les soldes après confirmation "
            "par le bénéficiaire.",
        )
        return redirect(self.detail_url(settlement))


class SettlementDetailView(GroupSettlementMixin, View):
    template_name = "settlements/detail.html"

    def get(self, request, gid, sid):
        settlement = self.get_settlement()
        plan = RepaymentPlan.objects.filter(settlement=settlement).first()
        user = request.user
        context = {
            "group": self.get_group(),
            "settlement": settlement,
            "plan": plan,
            "installments": plan.installments.all() if plan else [],
            "prediction": plan.latest_prediction() if plan else None,
            "is_receiver": settlement.receiver.user_id == user.pk,
            "is_payer": settlement.payer.user_id == user.pk,
            "can_manage_plan": services.can_manage_plan(settlement, user),
            "can_predict": plan is not None and (
                services.is_party(settlement, user) or is_admin(user, settlement.group)
            ),
            "reminder_form": ReminderForm(),
        }
        context["explanation"] = (
            services.explain_prediction(context["prediction"]) if context["prediction"] else []
        )
        context["can_confirm"] = context["is_receiver"] and settlement.is_pending and (
            plan is None or plan.status != RepaymentPlan.Status.ACTIVE
        )
        return render(request, self.template_name, context)


class SettlementDecisionView(GroupSettlementMixin, View):
    """POST uniquement : confirmer ou rejeter (bénéficiaire seul, sinon 403)."""

    action = None
    http_method_names = ["post"]

    def post(self, request, gid, sid):
        settlement = self.get_settlement()
        try:
            if self.action == "confirm":
                services.confirm_settlement(settlement, request.user)
                messages.success(request, "Remboursement confirmé : les soldes sont mis à jour.")
            else:
                services.reject_settlement(settlement, request.user)
                messages.success(request, "Remboursement rejeté.")
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        return redirect(self.detail_url(settlement))


class PlanFormView(GroupSettlementMixin, View):
    """Création (plan absent) ou modification de l'échéancier d'un remboursement."""

    template_name = "settlements/plan_form.html"
    editing = False

    def get_objects(self):
        settlement = self.get_settlement()
        if not services.can_manage_plan(settlement, self.request.user):
            raise PermissionDenied
        return settlement, RepaymentPlan.objects.filter(settlement=settlement).first()

    def wrong_mode(self, settlement, plan):
        """Un seul échéancier par remboursement : création s'il n'existe pas, sinon modification."""
        if (plan is None) != self.editing:
            return None
        text = "Ce remboursement a déjà un échéancier." if plan else "Aucun échéancier à modifier."
        messages.info(self.request, text)
        return redirect(self.detail_url(settlement))

    def render_form(self, settlement, form):
        return render(
            self.request,
            self.template_name,
            {"group": self.get_group(), "settlement": settlement, "form": form, "editing": self.editing},
        )

    def get(self, request, gid, sid):
        settlement, plan = self.get_objects()
        return self.wrong_mode(settlement, plan) or self.render_form(
            settlement, RepaymentPlanForm(instance=plan)
        )

    def post(self, request, gid, sid):
        settlement, plan = self.get_objects()
        if response := self.wrong_mode(settlement, plan):
            return response
        form = RepaymentPlanForm(request.POST, instance=plan)
        if not form.is_valid():
            return self.render_form(settlement, form)
        plan = form.save(commit=False)
        plan.settlement = settlement
        try:
            services.save_plan(plan)
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
            return self.render_form(settlement, form)
        messages.success(request, "Échéancier enregistré et risque de retard calculé.")
        return redirect(self.detail_url(settlement))


class PlanDeleteView(GroupSettlementMixin, View):
    template_name = "settlements/plan_confirm_delete.html"

    def get_plan(self):
        settlement = self.get_settlement()
        if not services.can_manage_plan(settlement, self.request.user):
            raise PermissionDenied
        return get_object_or_404(RepaymentPlan, settlement=settlement)

    def get(self, request, gid, sid):
        plan = self.get_plan()
        return render(request, self.template_name, {"group": self.get_group(), "plan": plan})

    def post(self, request, gid, sid):
        plan = self.get_plan()
        settlement = plan.settlement
        try:
            services.delete_plan(plan)
            messages.success(request, "Échéancier supprimé.")
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        return redirect(self.detail_url(settlement))


class InstallmentPayView(GroupSettlementMixin, View):
    template_name = "settlements/installment_pay.html"

    def get_installment(self):
        installment = get_object_or_404(
            Installment.objects.select_related("plan__settlement__payer", "plan__settlement__group"),
            pk=self.kwargs["iid"],
            plan__settlement__group=self.get_group(),
        )
        if installment.plan.settlement.payer.user_id != self.request.user.pk:
            raise PermissionDenied
        return installment

    def render_form(self, installment, form):
        return render(
            self.request,
            self.template_name,
            {"group": self.get_group(), "installment": installment, "form": form},
        )

    def get(self, request, gid, iid):
        installment = self.get_installment()
        form = InstallmentPaymentForm(installment=installment, initial={"amount": installment.remaining})
        return self.render_form(installment, form)

    def post(self, request, gid, iid):
        installment = self.get_installment()
        form = InstallmentPaymentForm(request.POST, installment=installment)
        if not form.is_valid():
            return self.render_form(installment, form)
        try:
            services.pay_installment(installment, request.user, form.cleaned_data["amount"])
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
            return self.render_form(installment, form)
        messages.success(request, f"Paiement de {form.cleaned_data['amount']} enregistré.")
        return redirect(self.detail_url(installment.plan.settlement))


class RiskPredictView(GroupSettlementMixin, View):
    http_method_names = ["post"]

    def post(self, request, gid, sid):
        settlement = self.get_settlement()
        if not (services.is_party(settlement, request.user) or is_admin(request.user, settlement.group)):
            raise PermissionDenied
        plan = get_object_or_404(RepaymentPlan, settlement=settlement)
        prediction = services.predict_risk(plan)
        if prediction is None:
            messages.error(request, "Le modèle de risque n'est pas disponible pour le moment.")
        else:
            messages.success(request, f"Risque recalculé : {prediction.percent} % ({prediction.get_label_display().lower()}).")
        return redirect(self.detail_url(settlement))


class ReminderGenerateView(GroupSettlementMixin, View):
    """Appelé en fetch (JSON) : le LLM rédige la relance, avec repli sur un modèle de texte."""

    http_method_names = ["post"]

    def post(self, request, gid, sid):
        settlement = self.get_settlement()
        if settlement.receiver.user_id != request.user.pk:
            return JsonResponse({"error": "Seul le bénéficiaire peut relancer."}, status=403)
        if not settlement.is_pending:
            return JsonResponse({"error": "Ce remboursement est déjà traité."}, status=400)
        form = ReminderForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": "Ton invalide."}, status=400)
        return JsonResponse(services.generate_reminder(settlement, form.cleaned_data["tone"], user=request.user))


class SettlementParseView(GroupSettlementMixin, View):
    """IA : phrase en langage naturel → champs du formulaire (JSON, appelé en fetch)."""

    http_method_names = ["post"]

    def post(self, request, gid):
        text = (request.POST.get("text") or "").strip()
        if not text or len(text) > 500:
            return JsonResponse({"error": "Décrivez le remboursement en une phrase (500 caractères max)."}, status=400)
        result = services.parse_repayment_sentence(self.get_group(), self.get_membership(), text, user=request.user)
        return JsonResponse(result)


class PlanSuggestView(GroupSettlementMixin, View):
    """IA : propose les échéanciers abordables les moins risqués (JSON, appelé en fetch)."""

    http_method_names = ["post"]

    def post(self, request, gid, sid):
        settlement = self.get_settlement()
        if not services.can_manage_plan(settlement, request.user):
            return JsonResponse({"error": "Action non autorisée."}, status=403)
        try:
            capacity = Decimal(request.POST.get("capacity", "").replace(",", "."))
        except InvalidOperation:
            capacity = Decimal("0")
        if capacity <= 0:
            return JsonResponse({"error": "Indiquez un montant mensuel strictement positif."}, status=400)
        try:
            return JsonResponse(services.suggest_plans(settlement, capacity))
        except services.risk.RiskModelUnavailable as exc:
            return JsonResponse({"error": str(exc)}, status=503)


class PlanExportView(GroupSettlementMixin, View):
    """Téléchargement de l'échéancier en PDF ou en calendrier .ics (membres du groupe)."""

    fmt = "pdf"

    def get(self, request, gid, sid):
        plan = get_object_or_404(RepaymentPlan, settlement=self.get_settlement())
        filename = f"echeancier-{plan.settlement.group_id}-{plan.settlement_id}"
        if self.fmt == "ics":
            response = HttpResponse(exports.plan_ics(plan, host=request.get_host()), content_type="text/calendar; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="{filename}.ics"'
        else:
            response = HttpResponse(exports.plan_pdf(plan), content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
        return response


class ReminderSendView(GroupSettlementMixin, View):
    http_method_names = ["post"]

    def post(self, request, gid, sid):
        settlement = self.get_settlement()
        form = ReminderSendForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Le message de relance est vide ou trop long (600 caractères max).")
            return redirect(self.detail_url(settlement))
        try:
            sent = services.send_reminder(settlement, request.user, form.cleaned_data["message"])
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
            return redirect(self.detail_url(settlement))
        if sent:
            messages.success(request, "Relance envoyée au payeur.")
        else:
            messages.warning(
                request,
                "Les notifications (M6) ne sont pas encore disponibles : copiez le message pour l'envoyer.",
            )
        return redirect(self.detail_url(settlement))
