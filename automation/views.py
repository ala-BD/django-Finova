from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from automation.forms import RecurringExpenseForm, ReminderRuleForm
from core.markdown import render_markdown
from automation.models import ChatMessage, Notification, RecurringExpense, ReminderRule
from automation.services import (
    AssistantRequestError,
    IntegrationUnavailable,
    answer_assistant_question,
)
from groups.services import is_admin, is_member


def _group_model():
    from django.apps import apps

    try:
        return apps.get_model("groups", "Group")
    except LookupError as exc:
        raise IntegrationUnavailable("Le modèle de groupe M1 n'est pas encore disponible.") from exc


def _get_group_or_unavailable(request, gid):
    try:
        Group = _group_model()
    except IntegrationUnavailable as exc:
        return None, HttpResponse(str(exc), status=503)
    group = get_object_or_404(Group, pk=gid)
    if not is_member(request.user, group):
        return None, HttpResponseForbidden("Vous n'avez pas accès à ce groupe.")
    return group, None


def _can_manage(user, group, expense):
    return expense.owner_id == user.pk or is_admin(user, group)


@login_required
def recurring_expense_list(request, gid):
    group, error = _get_group_or_unavailable(request, gid)
    if error:
        return error
    recurring = RecurringExpense.objects.filter(group_id=group.pk)
    return render(
        request,
        "automation/recurring_list.html",
        {"group": group, "recurring": recurring},
    )


@login_required
def recurring_expense_detail(request, gid, pk):
    group, error = _get_group_or_unavailable(request, gid)
    if error:
        return error
    expense = get_object_or_404(RecurringExpense, pk=pk, group_id=group.pk)
    return render(
        request,
        "automation/recurring_detail.html",
        {
            "group": group,
            "expense": expense,
            "reminder_rules": expense.reminder_rules.all(),
        },
    )


@login_required
def recurring_expense_create(request, gid):
    group, error = _get_group_or_unavailable(request, gid)
    if error:
        return error
    form = RecurringExpenseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.group_id = group.pk
        expense.owner = request.user
        expense.save()
        messages.success(request, "La dépense récurrente a été créée.")
        return redirect("automation:recurring_detail", gid=group.pk, pk=expense.pk)
    return render(
        request,
        "automation/recurring_form.html",
        {"group": group, "form": form, "action": "Créer"},
    )


@login_required
def recurring_expense_update(request, gid, pk):
    group, error = _get_group_or_unavailable(request, gid)
    if error:
        return error
    expense = get_object_or_404(RecurringExpense, pk=pk, group_id=group.pk)
    if not _can_manage(request.user, group, expense):
        return HttpResponseForbidden("Seul le créateur ou un administrateur peut modifier cette dépense.")
    form = RecurringExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La dépense récurrente a été mise à jour.")
        return redirect("automation:recurring_detail", gid=group.pk, pk=expense.pk)
    return render(
        request,
        "automation/recurring_form.html",
        {
            "group": group,
            "form": form,
            "action": "Modifier",
            "expense": expense,
        },
    )


@login_required
def recurring_expense_delete(request, gid, pk):
    group, error = _get_group_or_unavailable(request, gid)
    if error:
        return error
    expense = get_object_or_404(RecurringExpense, pk=pk, group_id=group.pk)
    if not _can_manage(request.user, group, expense):
        return HttpResponseForbidden("Seul le créateur ou un administrateur peut supprimer cette dépense.")
    if request.method == "POST":
        expense.delete()
        messages.success(request, "La dépense récurrente a été supprimée.")
        return redirect("automation:recurring_list", gid=group.pk)
    return render(
        request,
        "automation/recurring_confirm_delete.html",
        {"group": group, "expense": expense},
    )


@login_required
def reminder_rule_create(request, gid, pk):
    group, error = _get_group_or_unavailable(request, gid)
    if error:
        return error
    expense = get_object_or_404(RecurringExpense, pk=pk, group_id=group.pk)
    if not _can_manage(request.user, group, expense):
        return HttpResponseForbidden("Vous ne pouvez pas modifier les rappels de cette dépense.")
    form = ReminderRuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rule = form.save(commit=False)
        rule.recurring_expense = expense
        rule.save()
        messages.success(request, "La règle de rappel a été créée.")
        return redirect("automation:recurring_detail", gid=group.pk, pk=expense.pk)
    return render(
        request,
        "automation/reminder_form.html",
        {"group": group, "expense": expense, "form": form, "action": "Créer"},
    )


@login_required
def reminder_rule_update(request, gid, pk, rule_id):
    group, error = _get_group_or_unavailable(request, gid)
    if error:
        return error
    expense = get_object_or_404(RecurringExpense, pk=pk, group_id=group.pk)
    if not _can_manage(request.user, group, expense):
        return HttpResponseForbidden("Vous ne pouvez pas modifier les rappels de cette dépense.")
    rule = get_object_or_404(expense.reminder_rules, pk=rule_id)
    form = ReminderRuleForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La règle de rappel a été mise à jour.")
        return redirect("automation:recurring_detail", gid=group.pk, pk=expense.pk)
    return render(
        request,
        "automation/reminder_form.html",
        {"group": group, "expense": expense, "form": form, "action": "Modifier"},
    )


@login_required
def reminder_rule_delete(request, gid, pk, rule_id):
    group, error = _get_group_or_unavailable(request, gid)
    if error:
        return error
    expense = get_object_or_404(RecurringExpense, pk=pk, group_id=group.pk)
    if not _can_manage(request.user, group, expense):
        return HttpResponseForbidden("Vous ne pouvez pas supprimer les rappels de cette dépense.")
    rule = get_object_or_404(expense.reminder_rules, pk=rule_id)
    if request.method == "POST":
        rule.delete()
        messages.success(request, "La règle de rappel a été supprimée.")
        return redirect("automation:recurring_detail", gid=group.pk, pk=expense.pk)
    return render(
        request,
        "automation/reminder_confirm_delete.html",
        {"group": group, "expense": expense, "rule": rule},
    )


@login_required
def notifications_list(request):
    notification_list = Notification.objects.filter(user=request.user).order_by("-created_at")
    page = Paginator(notification_list, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "automation/notifications.html",
        {"notifications": page, "unread_count": notification_list.filter(is_read=False).count()},
    )


@login_required
@require_POST
def mark_notification_read(request, notification_id):
    notification = get_object_or_404(
        Notification,
        pk=notification_id,
        user=request.user,
    )
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    messages.success(request, "Notification marquée comme lue.")
    return redirect("automation:notifications")


@login_required
def assistant_home(request):
    # 20 derniers échanges, affichés dans l'ordre chronologique (comme une conversation)
    history = list(ChatMessage.objects.filter(user=request.user).order_by("-created_at")[:20])[::-1]
    return render(request, "automation/assistant.html", {"history": history})


@login_required
@require_POST
def assistant_chat(request):
    question = (request.POST.get("question") or "").strip()
    if not question or len(question) > 2000:
        return JsonResponse(
            {"error": "La question doit contenir entre 1 et 2 000 caractères."},
            status=400,
        )
    try:
        message = answer_assistant_question(request.user, question)
    except AssistantRequestError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except IntegrationUnavailable as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except NotImplementedError:
        return JsonResponse(
            {"error": "Cette information n'est pas encore disponible : le module concerné est en cours de développement."},
            status=503,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(
        {
            "answer": message.answer,
            "answer_html": render_markdown(message.answer),
            "message_id": message.pk,
            "created_at": timezone.localtime(message.created_at).strftime("%d/%m/%Y %H:%M"),
        },
    )
