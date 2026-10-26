from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DeleteView, FormView, ListView, TemplateView, UpdateView

from core.permissions import GroupAdminRequiredMixin, GroupMemberRequiredMixin
from expenses.services import get_group_expenses
from groups.services import is_admin

from . import services
from .forms import BudgetForm, CategoryDeleteForm, CategoryForm, SimulationForm
from .models import Budget, Category


def decimal_str(value, places=3):
    if value is None:
        return None
    return f"{Decimal(value):.{places}f}"


class GroupContextMixin:
    """Ajoute le groupe et le rôle de l'utilisateur au contexte des gabarits."""

    active_tab = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.get_group()
        context.update(
            group=group,
            is_group_admin=is_admin(self.request.user, group),
            active_tab=self.active_tab,
        )
        return context


class BudgetsOverviewView(LoginRequiredMixin, TemplateView):
    """Point d'entrée « Budgets » : liste des groupes de l'utilisateur."""

    template_name = "budgets/overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        month = services.current_month()
        rows = []
        for group in self.request.user.get_groups():
            statuses = services.group_budget_statuses(group, month)
            rows.append(
                {
                    "group": group,
                    "budget_count": len(statuses),
                    "alerts": sum(1 for status in statuses if status["level"] in ("warning", "over")),
                }
            )
        context.update(rows=rows, month=month)
        return context


# --- Budgets ------------------------------------------------------------------------


class BudgetListView(GroupMemberRequiredMixin, GroupContextMixin, TemplateView):
    template_name = "budgets/budget_list.html"
    active_tab = "budgets"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.get_group()
        month = services.parse_month(self.request.GET.get("month")) or services.current_month()
        is_current = month == services.current_month()
        statuses = services.group_budget_statuses(group, month)
        for status in statuses:
            status["forecast"] = (
                services.forecast_budget(status["budget"], user=self.request.user) if is_current else None
            )
        budgeted = {status["category"].pk for status in statuses}
        context.update(
            month=month,
            is_current_month=is_current,
            previous_month=services.add_months(month, -1),
            next_month=services.add_months(month, 1),
            statuses=statuses,
            total_limit=sum((s["effective_limit"] for s in statuses), Decimal("0")),
            total_consumed=sum((s["consumed"] for s in statuses), Decimal("0")),
            unbudgeted_categories=Category.objects.filter(group=group).exclude(pk__in=budgeted),
        )
        return context


class BudgetFormMixin(GroupContextMixin):
    model = Budget
    form_class = BudgetForm
    template_name = "budgets/budget_form.html"
    active_tab = "budgets"

    def get_queryset(self):
        return Budget.objects.filter(group=self.get_group()).select_related("category")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["group"] = self.get_group()
        return kwargs

    def get_success_url(self):
        return reverse("budgets:list", kwargs={"gid": self.get_group().pk}) + f"?month={self.object.month:%Y-%m}"


class BudgetCreateView(GroupAdminRequiredMixin, BudgetFormMixin, CreateView):
    def get_initial(self):
        initial = super().get_initial()
        initial["month"] = services.parse_month(self.request.GET.get("month")) or services.current_month()
        category_id = self.request.GET.get("category")
        if category_id and category_id.isdigit():
            initial["category"] = Category.objects.filter(group=self.get_group(), pk=category_id).first()
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        services.check_budget_alerts(self.object)
        messages.success(self.request, f"Budget « {self.object.category.name} » créé.")
        return response


class BudgetUpdateView(GroupAdminRequiredMixin, BudgetFormMixin, UpdateView):
    pk_url_kwarg = "pk"

    def form_valid(self, form):
        response = super().form_valid(form)
        services.reset_alerts_below_threshold(self.object)
        services.check_budget_alerts(self.object)
        messages.success(self.request, f"Budget « {self.object.category.name} » mis à jour.")
        return response


class BudgetDeleteView(GroupAdminRequiredMixin, GroupContextMixin, DeleteView):
    template_name = "budgets/budget_confirm_delete.html"
    active_tab = "budgets"

    def get_queryset(self):
        return Budget.objects.filter(group=self.get_group()).select_related("category")

    def get_success_url(self):
        return reverse("budgets:list", kwargs={"gid": self.get_group().pk}) + f"?month={self.object.month:%Y-%m}"

    def form_valid(self, form):
        messages.success(
            self.request,
            f"Budget « {self.object.category.name} » supprimé. Les dépenses ne sont pas affectées.",
        )
        return super().form_valid(form)


# --- Catégories ----------------------------------------------------------------------


class CategoryListView(GroupMemberRequiredMixin, GroupContextMixin, ListView):
    template_name = "budgets/category_list.html"
    context_object_name = "categories"
    active_tab = "categories"

    def get_queryset(self):
        return Category.objects.filter(group=self.get_group()).annotate(
            expense_count=Count("expenses", distinct=True),
            budget_count=Count("budgets", distinct=True),
        )


class CategoryFormMixin(GroupContextMixin):
    model = Category
    form_class = CategoryForm
    template_name = "budgets/category_form.html"
    active_tab = "categories"

    def get_queryset(self):
        return Category.objects.filter(group=self.get_group())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["group"] = self.get_group()
        return kwargs

    def get_success_url(self):
        return reverse("budgets:categories", kwargs={"gid": self.get_group().pk})


class CategoryCreateView(GroupAdminRequiredMixin, CategoryFormMixin, CreateView):
    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Catégorie « {self.object.name} » créée.")
        return response


class CategoryUpdateView(GroupAdminRequiredMixin, CategoryFormMixin, UpdateView):
    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Catégorie « {self.object.name} » mise à jour.")
        return response


class CategoryDeleteView(GroupAdminRequiredMixin, GroupContextMixin, FormView):
    """
    Suppression refusée tant que des dépenses sont rattachées, sauf réaffectation.
    (Expense.category est en PROTECT : la base refuse aussi une suppression directe.)
    """

    form_class = CategoryDeleteForm
    template_name = "budgets/category_confirm_delete.html"
    active_tab = "categories"

    def get_category(self):
        if not hasattr(self, "_category"):
            self._category = get_object_or_404(Category, pk=self.kwargs["pk"], group=self.get_group())
        return self._category

    def expenses(self):
        return get_group_expenses(self.get_group(), category=self.get_category())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(category=self.get_category(), expense_count=self.expenses().count())
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        category = self.get_category()
        context.update(
            category=category,
            expense_count=self.expenses().count(),
            budget_count=category.budgets.count(),
        )
        return context

    def form_valid(self, form):
        category = self.get_category()
        target = form.cleaned_data.get("reassign_to")
        with transaction.atomic():
            moved = self.expenses().update(category=target) if target else 0
            name = category.name
            category.delete()
        if moved:
            messages.success(
                self.request,
                f"Catégorie « {name} » supprimée ; {moved} dépense(s) réaffectée(s) à « {target.name} ».",
            )
        else:
            messages.success(self.request, f"Catégorie « {name} » supprimée.")
        return redirect("budgets:categories", gid=self.get_group().pk)


# --- Simulateur et IA ------------------------------------------------------------------


def wants_json(request):
    return request.GET.get("format") == "json" or "application/json" in request.headers.get("Accept", "")


def simulation_payload(result):
    return {
        "category": result["category"],
        "months_of_history": result["months_of_history"],
        "monthly_average": decimal_str(result["monthly_average"]),
        "reduction_percent": decimal_str(result["reduction_percent"], 1),
        "new_monthly_spend": decimal_str(result["new_monthly_spend"]),
        "monthly_saving": decimal_str(result["monthly_saving"]),
        "savings_goal": decimal_str(result["savings_goal"]),
        "months_needed": result["months_needed"],
        "reached_on": result["reached_on"].strftime("%Y-%m") if result["reached_on"] else None,
    }


class SimulateView(GroupMemberRequiredMixin, GroupContextMixin, TemplateView):
    """
    Simulateur « et si ? » : lecture seule, donc en GET.
    Avec JavaScript, le formulaire appelle cette URL en JSON (fetch) et la page
    ne se recharge pas ; sans JavaScript, le résultat est rendu côté serveur.
    """

    template_name = "budgets/simulate.html"
    active_tab = "simulate"

    def get(self, request, *args, **kwargs):
        group = self.get_group()
        bound = "category" in request.GET
        form = SimulationForm(request.GET if bound else None, group=group)
        result = None
        if bound and form.is_valid():
            result = services.simulate_savings(
                group,
                form.cleaned_data["category"],
                form.cleaned_data["reduction_percent"],
                form.cleaned_data["savings_goal"],
            )
        if wants_json(request):
            if not bound or not form.is_valid():
                return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
            return JsonResponse({"ok": True, "result": simulation_payload(result)})
        return self.render_to_response(self.get_context_data(form=form, result=result))


class RecommendView(GroupMemberRequiredMixin, View):
    """Recommandation de limite (JSON), appelée depuis le formulaire de budget."""

    def get(self, request, *args, **kwargs):
        group = self.get_group()
        category_id = request.GET.get("category", "")
        category = (
            Category.objects.filter(group=group, pk=category_id).first() if category_id.isdigit() else None
        )
        if category is None:
            return JsonResponse({"ok": False, "error": "Choisissez d'abord une catégorie."}, status=400)
        month = services.parse_month(request.GET.get("month")) or services.current_month()
        result = services.recommend_budget(group, category, month, user=request.user)
        return JsonResponse(
            {
                "ok": True,
                "suggested_limit": decimal_str(result["suggested_limit"]),
                "average": decimal_str(result["average"]),
                "months_used": result["months_used"],
                "explanation": result["explanation"],
                "source": result["source"],
            }
        )
