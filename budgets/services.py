"""
Logique métier M4 (Linda).

Contrat exposé aux autres modules : get_budget_status(group, category, month).
Les dépenses sont lues uniquement via expenses.services.get_group_expenses, et
les notifications passent par automation.services.notify.
"""

from __future__ import annotations

import calendar
import logging
import math
import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import ExtractDay, TruncMonth
from django.urls import reverse
from django.utils import timezone

from core.models import AIRequestLog
from expenses.services import get_group_expenses

from . import forecasting
from .models import Budget, BudgetForecast

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
ALERT_THRESHOLDS = (80, 100)
CARRY_OVER_MAX_DEPTH = 12
HISTORY_MONTHS = 6


# --- Mois ---------------------------------------------------------------------


def first_of_month(day: date) -> date:
    return day.replace(day=1)


def add_months(month: date, delta: int) -> date:
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def month_bounds(month: date) -> tuple[date, date]:
    month = first_of_month(month)
    last_day = calendar.monthrange(month.year, month.month)[1]
    return month, month.replace(day=last_day)


def parse_month(value: str | None) -> date | None:
    """« 2026-10 » → date(2026, 10, 1) ; None si la valeur est absente ou invalide."""
    if not value:
        return None
    try:
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)
    except (TypeError, ValueError):
        return None


def current_month() -> date:
    return first_of_month(timezone.localdate())


# --- Consommation, report et statut ----------------------------------------------


def consumed_amount(group, category, month: date) -> Decimal:
    """Somme des dépenses de la catégorie sur le mois, agrégée par la base (Sum)."""
    total = get_group_expenses(group, period=month_bounds(month), category=category).aggregate(
        total=Sum("amount")
    )["total"]
    return total or ZERO


def carried_over_amount(budget: Budget, _depth: int = 0) -> Decimal:
    """Reste du mois précédent reporté sur ce budget (0 si pas de report)."""
    if _depth >= CARRY_OVER_MAX_DEPTH:
        return ZERO
    previous = (
        Budget.objects.filter(
            category=budget.category, month=add_months(budget.month, -1), carry_over=True
        )
        .select_related("group", "category")
        .first()
    )
    if previous is None:
        return ZERO
    previous_limit = previous.limit_amount + carried_over_amount(previous, _depth + 1)
    remaining = previous_limit - consumed_amount(previous.group, previous.category, previous.month)
    return max(remaining, ZERO)


def effective_limit(budget: Budget) -> Decimal:
    return budget.limit_amount + carried_over_amount(budget)


def percent_of(consumed: Decimal, limit: Decimal) -> Decimal:
    if not limit:
        return ZERO
    return (consumed * 100 / limit).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def level_for(percent: Decimal | None) -> str:
    if percent is None:
        return "none"
    if percent >= 100:
        return "over"
    if percent >= 80:
        return "warning"
    return "ok"


def get_budget_status(group, category, month: date) -> dict:
    """
    Contrat M4 (utilisé par M5 et M6).

    Retourne le pourcentage consommé et le niveau d'alerte d'une catégorie
    pour un mois. Sans budget défini, limit et percent valent None.
    """
    month = first_of_month(month)
    budget = Budget.objects.filter(group=group, category=category, month=month).first()
    consumed = consumed_amount(group, category, month)
    if budget is None:
        return {
            "budget": None,
            "category": category,
            "month": month,
            "limit": None,
            "carried_over": ZERO,
            "effective_limit": None,
            "consumed": consumed,
            "remaining": None,
            "percent": None,
            "level": "none",
        }
    carried = carried_over_amount(budget)
    limit = budget.limit_amount + carried
    percent = percent_of(consumed, limit)
    return {
        "budget": budget,
        "category": category,
        "month": month,
        "limit": budget.limit_amount,
        "carried_over": carried,
        "effective_limit": limit,
        "consumed": consumed,
        "remaining": limit - consumed,
        "percent": percent,
        "level": level_for(percent),
    }


def group_budget_statuses(group, month: date) -> list[dict]:
    budgets = Budget.objects.filter(group=group, month=first_of_month(month)).select_related(
        "category"
    )
    return [get_budget_status(group, budget.category, budget.month) for budget in budgets]


# --- Alertes 80 % / 100 % ------------------------------------------------------------


def _notify_members(budget: Budget, threshold: int, percent: Decimal) -> None:
    from automation.services import notify

    link = reverse("budgets:list", kwargs={"gid": budget.group_id}) + f"?month={budget.month:%Y-%m}"
    if threshold >= 100:
        message = f"Budget « {budget.category.name} » dépassé ({percent} %) pour {budget.month:%m/%Y}."
    else:
        message = f"Budget « {budget.category.name} » consommé à {percent} % pour {budget.month:%m/%Y}."
    for membership in budget.group.memberships.filter(is_active=True).select_related("user"):
        notify(membership.user, f"budget_alert_{threshold}", message, link)


def check_budget_alerts(budget: Budget) -> list[int]:
    """
    Envoie au plus une notification par seuil et par mois.

    Le drapeau est levé par un UPDATE conditionnel (… WHERE alert_80_sent = false) :
    si deux dépenses arrivent en même temps, une seule requête gagne, donc un
    seul envoi. Si l'envoi échoue, le drapeau est rabaissé pour réessayer plus tard.
    """
    status = get_budget_status(budget.group, budget.category, budget.month)
    percent = status["percent"] or ZERO
    sent = []
    for threshold in ALERT_THRESHOLDS:
        if percent < threshold:
            continue
        flag = f"alert_{threshold}_sent"
        won = Budget.objects.filter(pk=budget.pk, **{flag: False}).update(**{flag: True})
        if not won:
            continue
        try:
            _notify_members(budget, threshold, percent)
        except NotImplementedError:
            logger.warning("notify (M6) indisponible : alerte %s %% reportée.", threshold)
            Budget.objects.filter(pk=budget.pk).update(**{flag: False})
            continue
        setattr(budget, flag, True)
        sent.append(threshold)
    return sent


def reset_alerts_below_threshold(budget: Budget) -> None:
    """Après une hausse de limite, une alerte redevient possible si on repasse sous le seuil."""
    percent = get_budget_status(budget.group, budget.category, budget.month)["percent"] or ZERO
    updates = {}
    if percent < 80 and budget.alert_80_sent:
        updates["alert_80_sent"] = False
    if percent < 100 and budget.alert_100_sent:
        updates["alert_100_sent"] = False
    if updates:
        Budget.objects.filter(pk=budget.pk).update(**updates)
        for field, value in updates.items():
            setattr(budget, field, value)


def check_alerts_for_expense(expense) -> list[int]:
    """Appelé à l'enregistrement d'une dépense (signal post_save sur Expense)."""
    if not expense.category_id:
        return []
    budget = (
        Budget.objects.filter(category_id=expense.category_id, month=first_of_month(expense.expense_date))
        .select_related("group", "category")
        .first()
    )
    if budget is None:
        return []
    return check_budget_alerts(budget)


# --- Historique -------------------------------------------------------------------


def monthly_history(group, category, before_month: date, months: int = HISTORY_MONTHS) -> list[dict]:
    """
    Totaux des `months` mois précédant `before_month` (du plus ancien au plus récent).

    Les mois antérieurs à la première dépense connue sont ignorés, pour ne pas
    tirer la moyenne vers zéro sur un groupe récent.
    """
    start = add_months(before_month, -months)
    end = before_month
    rows = (
        get_group_expenses(group, period=(start, end - timedelta(days=1)), category=category)
        .order_by()  # sans tri : sinon les champs triés entrent dans le GROUP BY (une ligne par dépense)
        .annotate(month=TruncMonth("expense_date"))
        .values("month")
        .annotate(total=Sum("amount"))
    )
    totals = {first_of_month(row["month"]): row["total"] for row in rows}
    if not totals:
        return []
    first_known = min(totals)
    history = []
    month = max(start, first_known)
    while month < end:
        history.append({"month": month, "total": totals.get(month, ZERO)})
        month = add_months(month, 1)
    return history


def cumulative_at_day(group, category, months: list[date], day: int) -> list[Decimal]:
    """Pour chaque mois, le cumul des dépenses du jour 1 au jour `day` inclus."""
    results = []
    for month in months:
        first, last = month_bounds(month)
        cutoff = first.replace(day=min(day, last.day))
        total = get_group_expenses(group, period=(first, cutoff), category=category).aggregate(
            total=Sum("amount")
        )["total"]
        results.append(total or ZERO)
    return results


def daily_amounts(group, category, month: date, until_day: int) -> list[Decimal]:
    first, last = month_bounds(month)
    cutoff = first.replace(day=min(until_day, last.day))
    rows = (
        get_group_expenses(group, period=(first, cutoff), category=category)
        .order_by()
        .annotate(day=ExtractDay("expense_date"))
        .values("day")
        .annotate(total=Sum("amount"))
    )
    by_day = {row["day"]: row["total"] for row in rows}
    return [by_day.get(day, ZERO) for day in range(1, cutoff.day + 1)]


# --- Simulateur « et si ? » -------------------------------------------------------------


def simulate_savings(group, category, reduction_percent, savings_goal, today: date | None = None) -> dict:
    """
    Économie mensuelle = moyenne mensuelle de la catégorie × pourcentage de réduction.
    Délai = objectif / économie mensuelle, arrondi au mois supérieur.

    Exemple du rapport : 300 TND/mois, −20 % → 60 TND/mois ; objectif 360 TND → 6 mois.
    """
    today = today or timezone.localdate()
    reduction = Decimal(str(reduction_percent))
    goal = Decimal(str(savings_goal))
    history = monthly_history(group, category, first_of_month(today))
    totals = [row["total"] for row in history]
    average = (sum(totals) / len(totals)).quantize(Decimal("0.001")) if totals else ZERO
    monthly_saving = (average * reduction / 100).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    months_needed = None
    reached_on = None
    if monthly_saving > 0:
        months_needed = math.ceil(goal / monthly_saving)
        reached_on = add_months(first_of_month(today), months_needed)

    return {
        "category": category.name,
        "months_of_history": len(totals),
        "monthly_average": average,
        "reduction_percent": reduction,
        "new_monthly_spend": average - monthly_saving,
        "monthly_saving": monthly_saving,
        "savings_goal": goal,
        "months_needed": months_needed,
        "reached_on": reached_on,
    }


# --- IA : prévision de fin de mois ------------------------------------------------------


def _log_ai(feature: str, started: float, success: bool, user=None, error: str = "") -> int:
    latency = int((time.perf_counter() - started) * 1000)
    AIRequestLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        module="budgets",
        feature=feature,
        latency_ms=latency,
        success=success,
        error_message=error[:255],
    )
    return latency


def risk_level(probability: float | None, threshold: float) -> tuple[str, str]:
    """Faible sous le seuil du modèle (choisi par validation croisée), élevé à partir de 70 %."""
    if probability is None:
        return "unknown", "indisponible"
    if probability >= 0.7:
        return "high", "élevé"
    if probability >= threshold:
        return "medium", "modéré"
    return "low", "faible"


def group_pace_inputs(group, month: date, day: int) -> tuple[Decimal, Decimal]:
    """Dépensé par tout le groupe du 1er au jour d, et la moyenne habituelle à la même date."""
    first, last = month_bounds(month)
    cutoff = first.replace(day=min(day, last.day))
    spent = get_group_expenses(group, period=(first, cutoff)).aggregate(total=Sum("amount"))["total"] or ZERO
    first_known = (
        get_group_expenses(group).order_by("expense_date").values_list("expense_date", flat=True).first()
    )
    if first_known is None:
        return spent, ZERO
    history = []
    past = add_months(month, -1)
    while past >= first_of_month(first_known) and len(history) < HISTORY_MONTHS:
        history.append(past)
        past = add_months(past, -1)
    if not history:
        return spent, ZERO
    totals = []
    for past in history:
        past_first, past_last = month_bounds(past)
        past_cutoff = past_first.replace(day=min(day, past_last.day))
        totals.append(
            get_group_expenses(group, period=(past_first, past_cutoff)).aggregate(total=Sum("amount"))["total"]
            or ZERO
        )
    return spent, sum(totals) / len(totals)


def risk_factors(features: dict, day: int, days_in_month: int, months_of_history: int) -> list[str]:
    """Explications lisibles de la probabilité, tirées des variables du modèle."""
    factors = [f"{features['spent_ratio'] * 100:.0f} % du budget déjà consommé au jour {day} sur {days_in_month}"]
    if months_of_history:
        pace = features["pace_vs_usual"]
        if pace >= 1.1:
            factors.append(f"dépenses {(pace - 1) * 100:.0f} % au-dessus de l'habitude à la même date")
        elif pace <= 0.9:
            factors.append(f"dépenses {(1 - pace) * 100:.0f} % en dessous de l'habitude à la même date")
        else:
            factors.append("dépenses proches de l'habitude à la même date")
        over = round(features["history_over_rate"] * months_of_history)
        if over:
            factors.append(f"cette limite aurait été dépassée {over} mois sur les {months_of_history} derniers")
    group_pace = features["group_pace_vs_usual"]
    if group_pace >= 1.1:
        factors.append(f"le groupe dépense {(group_pace - 1) * 100:.0f} % de plus que d'habitude ce mois-ci")
    elif group_pace <= 0.9:
        factors.append(f"le groupe dépense {(1 - group_pace) * 100:.0f} % de moins que d'habitude ce mois-ci")
    return factors


def forecast_budget(budget: Budget, today: date | None = None, user=None, save: bool = True) -> dict | None:
    """
    Analyse IA d'un budget du mois en cours :
    - prévision du total de fin de mois (profil historique, budgets/forecasting.py) ;
    - fourchette à 80 % autour de cette prévision ;
    - probabilité de dépassement (modèle entraîné, budgets/ai_model.py) et ses facteurs.

    Retourne None pour un mois passé ou futur (la prévision n'a de sens que
    pendant le mois). Une seule BudgetForecast est gardée par budget et par jour.
    """
    from .ai_model import load_overrun_model, predict_overrun_probability

    today = today or timezone.localdate()
    if first_of_month(today) != budget.month:
        return None
    started = time.perf_counter()
    days_in_month = month_bounds(budget.month)[1].day
    history = monthly_history(budget.group, budget.category, budget.month)
    history_months = [row["month"] for row in history]
    history_totals = [row["total"] for row in history]
    history_cumulative = cumulative_at_day(budget.group, budget.category, history_months, today.day)
    result = forecasting.forecast_month_total(
        daily_amounts(budget.group, budget.category, budget.month, today.day),
        days_in_month,
        history_totals,
        history_cumulative,
    )
    limit = effective_limit(budget)

    group_spent, group_usual = group_pace_inputs(budget.group, budget.month, today.day)
    features = forecasting.overrun_features(
        result.spent_so_far,
        result.predicted_total,
        limit,
        today.day,
        days_in_month,
        history_totals,
        history_cumulative,
        group_spent=group_spent,
        group_usual_spent=group_usual,
    )
    bundle = load_overrun_model()
    if result.spent_so_far > limit:
        probability = 1.0  # déjà dépassé : rien à prédire
    else:
        probability = predict_overrun_probability(features)
    threshold = bundle["threshold"] if bundle else 0.5
    level, level_label = risk_level(probability, threshold)
    low = high = None
    if bundle:
        low, high = forecasting.forecast_interval(
            result.predicted_total, result.spent_so_far, features["day_fraction"], bundle["quantiles"]
        )
    latency = _log_ai("forecast", started, True, user)

    if save:
        with transaction.atomic():
            forecast = BudgetForecast.objects.filter(
                budget=budget, created_at__date=timezone.localdate()
            ).first()
            if forecast is None:
                forecast = BudgetForecast(budget=budget)
            forecast.predicted_total = result.predicted_total
            forecast.baseline_total = result.baseline_total
            forecast.interval_low = low
            forecast.interval_high = high
            forecast.overrun_probability = probability
            forecast.method = result.method
            forecast.save()

    return {
        "predicted_total": result.predicted_total,
        "baseline_total": result.baseline_total,
        "interval_low": low,
        "interval_high": high,
        "spent_so_far": result.spent_so_far,
        "method": result.method,
        "limit": limit,
        "likely_overrun": result.predicted_total > limit,
        "overrun_amount": max(result.predicted_total - limit, ZERO),
        "overrun_probability": probability,
        "risk_percent": round(probability * 100) if probability is not None else None,
        "risk_level": level,
        "risk_label": level_label,
        "factors": risk_factors(features, today.day, days_in_month, len(history_totals)),
        "features": features,
        "model_version": bundle["version"] if bundle else None,
        "latency_ms": latency,
    }


# --- IA : recommandation de budget -------------------------------------------------------


def fallback_explanation(category_name: str, average: Decimal, suggested: Decimal, months: int) -> str:
    return (
        f"Sur les {months} derniers mois, la catégorie « {category_name} » a coûté en moyenne "
        f"{average:.3f} par mois ; une limite de {suggested:.0f} laisse une marge d'environ 10 %."
    )


def recommend_budget(group, category, month: date, user=None) -> dict:
    """
    Limite conseillée = moyenne des 3 derniers mois + 10 %, arrondie à la dizaine.

    L'explication est rédigée par le LLM (core.ai.call_llm) ; si l'API est
    indisponible ou renvoie un texte incohérent, un texte statique est utilisé.
    """
    from core.ai import call_llm

    history = monthly_history(group, category, first_of_month(month), months=3)
    totals = [row["total"] for row in history]
    suggested = forecasting.recommend_limit(totals)
    if suggested is None:
        return {
            "suggested_limit": None,
            "average": None,
            "months_used": 0,
            "explanation": "Pas encore assez de dépenses dans cette catégorie pour recommander une limite.",
            "source": "none",
        }

    average = (sum(totals) / len(totals)).quantize(Decimal("0.001"))
    fallback = fallback_explanation(category.name, average, suggested, len(totals))
    prompt = (
        "Tu es l'assistant budgétaire de Finova. En une seule phrase en français, "
        "sans inventer d'autres chiffres, explique pourquoi la limite mensuelle "
        f"recommandée pour la catégorie « {category.name} » est de {suggested:.0f} {group.currency}, "
        f"sachant que la moyenne des {len(totals)} derniers mois est de {average:.0f} {group.currency} "
        "et qu'on ajoute une marge de 10 %."
    )
    explanation, source = fallback, "fallback"
    try:
        answer = call_llm("recommendation", prompt, module="budgets", user=user)
        text = answer.get("text", "") if isinstance(answer, dict) else str(answer or "")
        text = " ".join(text.split())
        if text and len(text) <= 400 and f"{suggested:.0f}" in text:
            explanation, source = text, "llm"
    except Exception as exc:  # l'IA n'est jamais bloquante
        logger.info("Recommandation : repli sur le texte statique (%s).", exc)

    return {
        "suggested_limit": suggested,
        "average": average,
        "months_used": len(totals),
        "explanation": explanation,
        "source": source,
    }


def analysis_payload(budget: Budget, analysis: dict | None) -> dict:
    """Représentation JSON d'une analyse (API /insight/)."""

    def money(value):
        return f"{value:.3f}" if value is not None else None

    payload = {
        "budget_id": budget.pk,
        "category": budget.category.name,
        "month": budget.month.strftime("%Y-%m"),
        "available": analysis is not None,
    }
    if analysis is None:
        payload["reason"] = "L'analyse IA ne concerne que le mois en cours."
        return payload
    payload.update(
        {
            "limit": money(analysis["limit"]),
            "spent_so_far": money(analysis["spent_so_far"]),
            "forecast": {
                "predicted_total": money(analysis["predicted_total"]),
                "interval_80": [money(analysis["interval_low"]), money(analysis["interval_high"])],
                "naive_baseline": money(analysis["baseline_total"]),
                "method": analysis["method"],
            },
            "overrun_risk": {
                "probability": round(analysis["overrun_probability"], 3)
                if analysis["overrun_probability"] is not None
                else None,
                "level": analysis["risk_level"],
                "label": analysis["risk_label"],
                "factors": analysis["factors"],
            },
            "model_version": analysis["model_version"],
            "latency_ms": analysis["latency_ms"],
        }
    )
    return payload


# --- Plan d'économie ----------------------------------------------------------------------


def savings_plan(group, goal, months: int, today: date | None = None):
    """
    Répartit un objectif d'épargne entre les catégories du groupe (budgets/planning.py),
    à partir des 6 derniers mois complets. Retourne (plan, {nom: Category}).
    """
    from .models import Category
    from .planning import plan_savings

    month = first_of_month(today or timezone.localdate())
    categories = {category.name: category for category in Category.objects.filter(group=group)}
    history = {
        name: [row["total"] for row in monthly_history(group, category, month)]
        for name, category in categories.items()
    }
    return plan_savings(history, goal, months), categories


def apply_savings_plan(group, plan, categories: dict, month: date) -> tuple[int, int]:
    """Crée ou met à jour les budgets du mois avec les limites du plan. Retourne (créés, modifiés)."""
    created = updated = 0
    with transaction.atomic():
        for row in plan.categories:
            if row.new_target <= 0:
                continue
            _, was_created = Budget.objects.update_or_create(
                category=categories[row.name],
                month=first_of_month(month),
                defaults={"group": group, "limit_amount": row.new_target},
            )
            created += was_created
            updated += not was_created
    return created, updated
