from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView

from analytics import detection, exports, services
from analytics.currency import SUPPORTED_CURRENCIES, convert_records
from analytics.datasource import get_user_expenses
from analytics.demo import CATEGORIES
from analytics.forms import ExpenseFilterForm, ReportForm, SavedFilterForm
from analytics.models import AnomalyFlag, Report, SavedFilter


def _display_currency(user, params):
    currency = params.get("currency") or getattr(user, "preferred_currency", "TND")
    return currency if currency in SUPPORTED_CURRENCIES else "TND"


def _load(user, params, source=None):
    """Dépenses de l'utilisateur, converties dans la devise d'affichage puis filtrées."""
    source = source or get_user_expenses(user)
    currency = _display_currency(user, params)
    converted, rates = convert_records(source.records, currency)
    return {
        "source": source,
        "currency": currency,
        "rates": rates,
        "records": services.apply_filters(converted, params),
    }


def _group_names(records):
    return {str(r.group_id): r.group_name for r in records}


@login_required
@require_GET
def dashboard(request):
    source = get_user_expenses(request.user)
    form = ExpenseFilterForm(request.GET or None, records=source.records)
    params = form.params() if form.is_bound and form.is_valid() else {}
    loaded = _load(request.user, params, source)
    records = loaded["records"]
    detections, detection_ms = detection.run_detection(records)

    query = urlencode(params)
    return render(
        request,
        "analytics/dashboard.html",
        {
            "form": form,
            "params": params,
            "query": query,
            "is_demo": loaded["source"].is_demo,
            "currency": loaded["currency"],
            "rates": loaded["rates"],
            "data": services.dashboard_data(records, CATEGORIES),
            "detections": detections[:8],
            "detection_count": len(detections),
            "detection_ms": detection_ms,
            "summary_month": services.latest_month(records),
            "active_filter": _active_filter(request),
        },
    )


def _active_filter(request):
    pk = request.GET.get("filtre")
    if not (pk and pk.isdigit()):
        return None
    return SavedFilter.objects.filter(user=request.user, pk=pk).first()


@login_required
@require_GET
def summary(request):
    """Résumé narratif du mois, chargé en arrière-plan (fetch) par le tableau de bord."""
    source = get_user_expenses(request.user)
    form = ExpenseFilterForm(request.GET or None, records=source.records)
    params = form.params() if form.is_bound and form.is_valid() else {}
    loaded = _load(request.user, params, source)
    month = request.GET.get("month") or services.latest_month(loaded["records"])
    if not month:
        return JsonResponse({"text": "Aucune dépense à résumer.", "source": "template", "reason": ""})
    agg = services.month_aggregates(loaded["records"], month, loaded["currency"], today=timezone.localdate())
    result = services.generate_narrative_summary(agg, user=request.user)
    result["month_label"] = agg["month_label"]
    return JsonResponse(result)


# --- Filtres sauvegardés ----------------------------------------------------

class OwnedQuerysetMixin(LoginRequiredMixin):
    """Chaque utilisateur n'accède qu'à ses propres objets (404 sinon)."""

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


class SavedFilterListView(OwnedQuerysetMixin, ListView):
    model = SavedFilter
    template_name = "analytics/filter_list.html"
    context_object_name = "filters"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        names = _group_names(get_user_expenses(self.request.user).records)
        context["rows"] = [(f, services.describe_params(f.params, names)) for f in context["filters"]]
        return context


class SavedFilterFormMixin(LoginRequiredMixin):
    form_class = SavedFilterForm
    template_name = "analytics/filter_form.html"
    success_url = reverse_lazy("analytics:filter_list")
    instance = None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["instance"] = self.instance
        kwargs["records"] = get_user_expenses(self.request.user).records
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["instance"] = self.instance
        return context


class SavedFilterCreateView(SavedFilterFormMixin, FormView):
    def get_initial(self):
        # Pré-rempli depuis le tableau de bord (« Enregistrer ce filtre »).
        return {key: self.request.GET[key] for key in services.FILTER_KEYS if self.request.GET.get(key)}

    def form_valid(self, form):
        saved = form.save()
        messages.success(self.request, f"Filtre « {saved.name} » enregistré.")
        return super().form_valid(form)


class SavedFilterUpdateView(SavedFilterFormMixin, FormView):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.instance = get_object_or_404(SavedFilter, pk=kwargs["pk"], user=request.user)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        saved = form.save()
        messages.success(self.request, f"Filtre « {saved.name} » modifié.")
        return super().form_valid(form)


class SavedFilterDeleteView(OwnedQuerysetMixin, DeleteView):
    model = SavedFilter
    template_name = "analytics/confirm_delete.html"
    success_url = reverse_lazy("analytics:filter_list")
    extra_context = {"kind": "le filtre", "cancel_url": reverse_lazy("analytics:filter_list")}

    def form_valid(self, form):
        messages.success(self.request, f"Filtre « {self.object.name} » supprimé.")
        return super().form_valid(form)


@login_required
@require_GET
def filter_apply(request, pk):
    saved = get_object_or_404(SavedFilter, pk=pk, user=request.user)
    query = urlencode({**saved.params, "filtre": saved.pk})
    return redirect(f"{reverse('analytics:dashboard')}?{query}")


# --- Rapports ----------------------------------------------------------------

class ReportListView(OwnedQuerysetMixin, ListView):
    model = Report
    template_name = "analytics/report_list.html"
    context_object_name = "reports"
    paginate_by = 20

    def get_queryset(self):
        return super().get_queryset().select_related("saved_filter")


class ReportCreateView(LoginRequiredMixin, CreateView):
    model = Report
    form_class = ReportForm
    template_name = "analytics/report_form.html"

    def get_initial(self):
        initial = {"format": Report.Format.CSV}
        pk = self.request.GET.get("filtre")
        if pk and pk.isdigit():
            initial["saved_filter"] = SavedFilter.objects.filter(user=self.request.user, pk=pk).first()
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        report = form.save(commit=False)
        report.user = self.request.user
        report.params = report.saved_filter.params if report.saved_filter else {}
        report.narrative_summary = _report_summary(self.request.user, report.params)
        report.save()
        _write_report_file(report)
        messages.success(self.request, f"Rapport « {report.title} » généré.")
        return redirect("analytics:report_detail", pk=report.pk)


class ReportDetailView(OwnedQuerysetMixin, DetailView):
    model = Report
    template_name = "analytics/report_detail.html"
    context_object_name = "report"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        names = _group_names(get_user_expenses(self.request.user).records)
        context["description"] = services.describe_params(self.object.params, names)
        return context


class ReportDeleteView(OwnedQuerysetMixin, DeleteView):
    model = Report
    template_name = "analytics/confirm_delete.html"
    success_url = reverse_lazy("analytics:report_list")
    extra_context = {"kind": "le rapport", "cancel_url": reverse_lazy("analytics:report_list")}

    def form_valid(self, form):
        self.object.file.delete(save=False)
        messages.success(self.request, f"Rapport « {self.object.title} » supprimé.")
        return super().form_valid(form)


@login_required
@require_GET
def report_download(request, pk):
    report = get_object_or_404(Report, pk=pk, user=request.user)
    if not report.file or not report.file.storage.exists(report.file.name):
        # Disque éphémère (Render) : le fichier est régénéré à partir des paramètres conservés.
        _write_report_file(report)
    return FileResponse(
        report.file.open("rb"),
        as_attachment=True,
        filename=report.download_name,
        content_type=report.content_type,
    )


def _report_summary(user, params):
    loaded = _load(user, params)
    month = services.latest_month(loaded["records"])
    if not month:
        return ""
    agg = services.month_aggregates(loaded["records"], month, loaded["currency"], today=timezone.localdate())
    return services.generate_narrative_summary(agg, user=user)["text"]


def _write_report_file(report):
    loaded = _load(report.user, report.params)
    if report.format == Report.Format.PDF:
        names = _group_names(loaded["source"].records)
        content = exports.build_pdf(
            loaded["records"],
            title=report.title,
            description=services.describe_params(report.params, names),
            summary=report.narrative_summary,
            currency=loaded["currency"],
        )
    else:
        content = exports.build_csv(loaded["records"])
    report.file.save(report.download_name, ContentFile(content), save=True)


# --- Anomalies et doublons ----------------------------------------------------

@login_required
@require_GET
def anomaly_list(request):
    status = request.GET.get("statut", "a-revoir")
    flags = AnomalyFlag.objects.filter(user=request.user)
    counts = {"to_review": flags.filter(reviewed=False).count(), "reviewed": flags.filter(reviewed=True).count()}
    if status == "revues":
        flags = flags.filter(reviewed=True)
    elif status != "toutes":
        status = "a-revoir"
        flags = flags.filter(reviewed=False)
    return render(
        request,
        "analytics/anomaly_list.html",
        {
            "flags": flags,
            "status": status,
            "counts": counts,
            "is_demo": get_user_expenses(request.user).is_demo,
        },
    )


@login_required
@require_POST
def anomaly_scan(request):
    loaded = _load(request.user, {})
    detections, elapsed_ms = detection.run_detection(loaded["records"])
    created = detection.sync_flags(request.user, detections)
    messages.success(
        request,
        f"Analyse terminée en {elapsed_ms} ms : {len(detections)} signalement(s), dont {created} nouveau(x).",
    )
    return redirect("analytics:anomaly_list")


@login_required
@require_POST
def anomaly_review(request, pk):
    flag = get_object_or_404(AnomalyFlag, pk=pk, user=request.user)
    flag.reviewed = not flag.reviewed
    flag.save(update_fields=["reviewed"])
    state = "marquée comme revue" if flag.reviewed else "remise à revoir"
    messages.success(request, f"« {flag.expense_label} » {state}.")
    return redirect(f"{reverse('analytics:anomaly_list')}?statut={request.POST.get('statut', 'a-revoir')}")
