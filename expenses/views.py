from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from core.permissions import GroupMemberRequiredMixin
from groups.models import Group, Membership
from .forms import ExpenseFilterForm, ExpenseForm, ExpenseItemForm
from .models import Expense, ExpenseItem, ItemClaim, ExpenseShare


class ExpenseGroupBaseMixin(GroupMemberRequiredMixin):
    """Socle commun pour toutes les vues de dépenses d'un groupe."""

    group_url_kwarg = "gid"

    def get_group(self):
        from groups.models import Group

        if self.group_object is None:
            gid = self.kwargs.get(self.group_url_kwarg)
            if gid is not None:
                self.group_object = get_object_or_404(Group, pk=gid)
            else:
                membership = Membership.objects.filter(
                    user=self.request.user, is_active=True
                ).first()
                if membership:
                    self.group_object = membership.group
                else:
                    raise PermissionDenied(_("Aucun groupe trouvé."))
        return self.group_object

    def get_queryset(self):
        return Expense.objects.filter(group=self.get_group())


    def get_user_membership(self) -> Membership:
        return get_object_or_404(
            Membership,
            group=self.get_group(),
            user=self.request.user,
            is_active=True,
        )

    def check_can_manage(self, expense: Expense):
        """Seul le payeur ou un admin de groupe peut modifier ou supprimer la dépense."""
        membership = self.get_user_membership()
        if not (membership.is_admin or expense.payer_id == membership.id):
            raise PermissionDenied(_("Seul le payeur ou un administrateur du groupe peut modifier cette dépense."))


class ExpenseListView(ExpenseGroupBaseMixin, ListView):
    """Lister les dépenses du groupe avec filtrage et pagination."""

    model = Expense
    template_name = "expenses/list.html"
    context_object_name = "expenses"
    paginate_by = 10

    def get_queryset(self):
        qs = super().get_queryset().select_related("payer__user", "category").prefetch_related("shares")
        self.filter_form = ExpenseFilterForm(self.request.GET, group=self.get_group())

        if self.filter_form.is_valid():
            q = self.filter_form.cleaned_data.get("q")
            category = self.filter_form.cleaned_data.get("category")
            payer = self.filter_form.cleaned_data.get("payer")
            period = self.filter_form.cleaned_data.get("period")

            if q:
                qs = qs.filter(Q(title__icontains=q))
            if category:
                qs = qs.filter(category=category)
            if payer:
                qs = qs.filter(payer=payer)
            if period and len(period) == 7 and "-" in period:
                try:
                    year, month = map(int, period.split("-"))
                    qs = qs.filter(expense_date__year=year, expense_date__month=month)
                except ValueError:
                    pass

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.get_group()
        context["group"] = group
        context["filter_form"] = getattr(self, "filter_form", ExpenseFilterForm(group=group))

        user_membership = self.get_user_membership()
        context["user_membership"] = user_membership

        # Statistiques synthétiques
        all_group_expenses = Expense.objects.filter(group=group)
        context["total_group_amount"] = all_group_expenses.aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.000")

        context["user_paid_total"] = all_group_expenses.filter(
            payer=user_membership
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.000")

        context["user_share_total"] = ExpenseShare.objects.filter(
            expense__group=group, member=user_membership
        ).aggregate(total=Sum("amount_due"))["total"] or Decimal("0.000")

        return context


class ExpenseDetailView(ExpenseGroupBaseMixin, DetailView):
    """Consulter le détail d'une dépense, ses articles et la répartition des parts."""

    model = Expense
    template_name = "expenses/detail.html"
    context_object_name = "expense"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        expense = self.object
        user_membership = self.get_user_membership()
        context["group"] = self.get_group()
        context["user_membership"] = user_membership
        context["items"] = expense.items.all().prefetch_related("claims__member__user")
        context["shares"] = expense.shares.all().select_related("member__user")
        context["can_manage"] = user_membership.is_admin or expense.payer_id == user_membership.id
        context["item_form"] = ExpenseItemForm()
        return context


class ExpenseCreateView(ExpenseGroupBaseMixin, CreateView):
    """Création d'une nouvelle dépense pour un groupe."""

    model = Expense
    form_class = ExpenseForm
    template_name = "expenses/form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["group"] = self.get_group()
        return kwargs

    def form_valid(self, form):
        form.instance.group = self.get_group()
        response = super().form_valid(form)
        self.object.compute_shares()
        messages.success(self.request, _("Dépense « %s » enregistrée avec succès.") % self.object.title)
        return response

    def get_success_url(self):
        return reverse("expenses:detail", kwargs={"gid": self.get_group().id, "pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.get_group()
        context["title"] = _("Ajouter une dépense")
        return context


class ExpenseUpdateView(ExpenseGroupBaseMixin, UpdateView):
    """Modification d'une dépense existante."""

    model = Expense
    form_class = ExpenseForm
    template_name = "expenses/form.html"

    def dispatch(self, request, *args, **kwargs):
        self.check_can_manage(self.get_object())
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["group"] = self.get_group()
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        self.object.compute_shares()
        messages.success(self.request, _("Dépense « %s » mise à jour avec succès.") % self.object.title)
        return response

    def get_success_url(self):
        return reverse("expenses:detail", kwargs={"gid": self.get_group().id, "pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.get_group()
        context["title"] = _("Modifier la dépense")
        context["expense"] = self.object
        return context


class ExpenseDeleteView(ExpenseGroupBaseMixin, DeleteView):
    """Suppression d'une dépense."""

    model = Expense
    template_name = "expenses/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        expense = self.get_object()
        self.check_can_manage(expense)
        if not expense.can_delete():
            messages.error(
                request,
                _("Impossible de supprimer cette dépense car un remboursement lié a déjà été confirmé."),
            )
            return redirect("expenses:detail", gid=self.get_group().id, pk=expense.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        messages.success(self.request, _("La dépense a été supprimée."))
        return reverse("expenses:list", kwargs={"gid": self.get_group().id})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group"] = self.get_group()
        context["expense"] = self.object
        return context


class ExpenseItemCreateView(ExpenseGroupBaseMixin, View):
    """Ajouter une ligne d'article de ticket à une dépense."""

    def post(self, request, gid, pk):
        expense = get_object_or_404(Expense, pk=pk, group_id=gid)
        self.check_can_manage(expense)

        form = ExpenseItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.expense = expense
            item.save()

            if expense.split_rule == Expense.SplitRule.BY_ITEM:
                expense.compute_shares()

            messages.success(request, _("Article « %s » ajouté au ticket.") % item.label)
        else:
            messages.error(request, _("Erreur lors de l'ajout de l'article : vérifiez les champs."))

        return redirect("expenses:detail", gid=gid, pk=pk)


class ItemClaimToggleView(ExpenseGroupBaseMixin, View):
    """Permet à un membre de réclamer ou dé-réclamer un article du ticket."""

    def post(self, request, gid, pk, item_id):
        expense = get_object_or_404(Expense, pk=pk, group_id=gid)
        item = get_object_or_404(ExpenseItem, pk=item_id, expense=expense)
        membership = self.get_user_membership()

        claim = ItemClaim.objects.filter(item=item, member=membership).first()
        if claim:
            claim.delete()
            messages.info(request, _("Vous ne réclamez plus l'article « %s ».") % item.label)
        else:
            ItemClaim.objects.create(item=item, member=membership)
            messages.success(request, _("Article « %s » réclamé !") % item.label)

        # Recalcul dynamique des parts
        expense.compute_shares()
        return redirect("expenses:detail", gid=gid, pk=pk)


class ExpenseCategorizeView(ExpenseGroupBaseMixin, View):
    """
    IA (1) : Endpoint AJAX de suggestion automatique de catégorie (Module M2 - Yahya).
    Reçoit un titre, exécute le classifieur scikit-learn et renvoie la catégorie adaptée.
    """

    def get(self, request, gid):
        from .services import predict_category

        title = request.GET.get("title", "").strip()[:200]
        if len(title) < 2:
            return JsonResponse({"category_id": None, "category_name": "", "confidence": 0.0,
                                 "confident": False, "exists": False, "label": ""})
        return JsonResponse(predict_category(title, group=self.get_group()))


class ExpenseNLPExtractView(ExpenseGroupBaseMixin, View):
    """
    IA (2) : Saisie en langage naturel (Module M2 - Yahya).
    Reçoit une phrase utilisateur, appelle call_llm (JSON strict validé, repli local par règles),
    et renvoie les champs structurés pour pré-remplir le formulaire.
    """

    def post(self, request, gid):
        from .ml import display_name, match_group_category
        from .nlp import parse_expense

        phrase = request.POST.get("phrase", "").strip()
        if not phrase:
            return JsonResponse({"success": False, "error": _("Veuillez saisir une phrase décrivant la dépense.")}, status=400)
        if len(phrase) > 500:
            return JsonResponse({"success": False, "error": _("Phrase trop longue (500 caractères maximum).")}, status=400)

        group = self.get_group()
        memberships = group.memberships.filter(is_active=True).select_related("user")
        members = [(m.id, [n for n in (m.user.first_name, m.user.last_name, m.user.username) if n])
                   for m in memberships]
        result = parse_expense(phrase, members, user=request.user)
        if not result["title"] and not result["amount"]:
            return JsonResponse({
                "success": False,
                "error": _("Je n'ai trouvé ni montant ni objet dans cette phrase. "
                           "Reformulez (ex : « 45 dinars resto hier ») ou remplissez le formulaire."),
            }, status=422)

        category = match_group_category(result["category"], group) if result["category"] else None
        names = {m.id: m.user.get_short_name() or m.user.username for m in memberships}
        return JsonResponse({
            "success": True,
            "title": result["title"],
            "amount": str(result["amount"]) if result["amount"] is not None else None,
            "currency": group.currency,
            "expense_date": result["expense_date"].isoformat() if result["expense_date"] else None,
            "label": result["category"] or "",
            "category_id": category.id if category else None,
            "category_name": category.name if category else (display_name(result["category"]) if result["category"] else ""),
            "exists": category is not None,
            "participants": [names[i] for i in result["participants"] if i in names],
            "unknown_participants": result["unknown_participants"],
            "missing": result["missing"],
            "source": result["source"],
            "latency_ms": result["latency_ms"],
        })

