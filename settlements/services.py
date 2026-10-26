"""M3 · Soldes et remboursements — logique métier (Imem).

Contrat exposé aux autres modules : compute_balances(group) -> {user_id: Decimal}
(utilisé par M1 pour « quitter un groupe » et par M6 pour l'assistant).
"""

import logging
import time
from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldError, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum

from core.ai import LLMError, call_llm
from groups.services import is_admin

from . import risk
from .models import RepaymentPlan, RiskPrediction, Settlement

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Soldes
# ---------------------------------------------------------------------------

def expense_flows(group) -> tuple[dict, dict] | None:
    """Montants payés et dus par utilisateur, lus dans M2.

    Contrat attendu de M2 (diagramme de classes, §4.2) :
    Expense(group, payer → Membership, amount) et
    ExpenseShare(expense, member → Membership, amount_due).
    Retourne None tant que ces modèles ne sont pas intégrés.
    """
    try:
        Expense = apps.get_model("expenses", "Expense")
        ExpenseShare = apps.get_model("expenses", "ExpenseShare")
    except LookupError:
        return None
    try:
        paid_rows = list(
            Expense.objects.filter(group=group)
            .values("payer__user_id")
            .annotate(total=Sum("amount"))
        )
        owed_rows = list(
            ExpenseShare.objects.filter(expense__group=group)
            .values("member__user_id")
            .annotate(total=Sum("amount_due"))
        )
    except (FieldError, ValueError) as exc:
        logger.warning("Modèles M2 incompatibles avec le contrat de M3 : %s", exc)
        return None
    paid = {row["payer__user_id"]: Decimal(str(row["total"] or 0)) for row in paid_rows}
    owed = {row["member__user_id"]: Decimal(str(row["total"] or 0)) for row in owed_rows}
    return paid, owed


def net_balances(paid: dict, owed: dict, transfers) -> dict:
    """Solde net = payé − dû + remboursé − reçu. Positif : créancier ; négatif : débiteur."""
    balances: dict = {}
    for user_id, amount in paid.items():
        balances[user_id] = balances.get(user_id, ZERO) + amount
    for user_id, amount in owed.items():
        balances[user_id] = balances.get(user_id, ZERO) - amount
    for payer_id, receiver_id, amount in transfers:
        balances[payer_id] = balances.get(payer_id, ZERO) + amount
        balances[receiver_id] = balances.get(receiver_id, ZERO) - amount
    return balances


def compute_balances(group) -> dict:
    """Solde net de chaque membre : {user_id: Decimal}, à partir des parts (M2)
    et des remboursements confirmés. Tous les membres actifs sont présents."""
    flows = expense_flows(group)
    paid, owed = flows if flows else ({}, {})
    transfers = Settlement.objects.filter(
        group=group, status=Settlement.Status.CONFIRMED
    ).values_list("payer__user_id", "receiver__user_id", "amount")
    balances = net_balances(paid, owed, transfers)
    for user_id in group.active_memberships().values_list("user_id", flat=True):
        balances.setdefault(user_id, ZERO)
    return balances


def simplify_debts(balances: dict) -> list[tuple]:
    """Au plus n−1 virements : à chaque étape, le plus gros débiteur rembourse le plus
    gros créancier du plus petit des deux montants, ce qui annule au moins un solde.
    Retourne [(débiteur, créancier, montant)]."""
    creditors = sorted(((b, k) for k, b in balances.items() if b > 0), key=_order)
    debtors = sorted(((-b, k) for k, b in balances.items() if b < 0), key=_order)
    transfers = []
    while creditors and debtors:
        credit, creditor = creditors.pop(0)
        debt, debtor = debtors.pop(0)
        pay = min(credit, debt)
        transfers.append((debtor, creditor, pay))
        if credit > pay:
            creditors.append((credit - pay, creditor))
            creditors.sort(key=_order)
        if debt > pay:
            debtors.append((debt - pay, debtor))
            debtors.sort(key=_order)
    return transfers


def _order(item):
    amount, key = item
    return (-amount, str(key))


def balance_overview(group) -> dict:
    """Données de la page des soldes : lignes par membre et virements simplifiés."""
    memberships = {m.user_id: m for m in group.active_memberships()}
    balances = compute_balances(group)
    rows = [
        {"membership": memberships[user_id], "balance": balance, "magnitude": abs(balance)}
        for user_id, balance in balances.items()
        if user_id in memberships
    ]
    rows.sort(key=lambda row: (row["balance"], str(row["membership"].user)))
    transfers = [
        {"debtor": memberships.get(d), "creditor": memberships.get(c), "amount": amount}
        for d, c, amount in simplify_debts(balances)
    ]
    return {
        "rows": rows,
        "transfers": transfers,
        "expenses_integrated": expense_flows(group) is not None,
    }


# ---------------------------------------------------------------------------
# Remboursements : création, confirmation, rejet
# ---------------------------------------------------------------------------

def _notify(membership, kind, message, link):
    """notify de M6 ; une notification manquée ne bloque jamais un remboursement."""
    try:
        from automation.services import notify

        notify(membership.user, kind, message, link)
        return True
    except Exception as exc:  # M6 pas encore intégré ou erreur d'envoi
        logger.info("Notification %s non envoyée : %s", kind, exc)
        return False


def settlement_link(settlement):
    return f"/groups/{settlement.group_id}/settlements/{settlement.pk}/"


@transaction.atomic
def create_settlement(group, payer, receiver, amount, note=""):
    settlement = Settlement(group=group, payer=payer, receiver=receiver, amount=amount, note=note)
    settlement.full_clean()
    settlement.save()
    _notify(
        receiver,
        "settlement_declared",
        f"{payer.user} déclare vous avoir remboursé {amount} {group.currency}. Confirmez la réception.",
        settlement_link(settlement),
    )
    return settlement


def confirm_settlement(settlement, user):
    """Seul le bénéficiaire peut confirmer ; changement irréversible."""
    if settlement.receiver.user_id != user.pk:
        raise PermissionDenied("Seul le bénéficiaire peut confirmer ce remboursement.")
    plan = getattr(settlement, "plan", None)
    if plan and plan.status == RepaymentPlan.Status.ACTIVE:
        raise ValidationError("L'échéancier n'est pas encore entièrement payé.")
    settlement.confirm()
    _notify(
        settlement.payer,
        "settlement_confirmed",
        f"{settlement.receiver.user} a confirmé votre remboursement de {settlement.amount}.",
        settlement_link(settlement),
    )


@transaction.atomic
def reject_settlement(settlement, user):
    if settlement.receiver.user_id != user.pk:
        raise PermissionDenied("Seul le bénéficiaire peut rejeter ce remboursement.")
    settlement.reject()
    RepaymentPlan.objects.filter(settlement=settlement).update(status=RepaymentPlan.Status.CANCELLED)
    _notify(
        settlement.payer,
        "settlement_rejected",
        f"{settlement.receiver.user} a rejeté votre remboursement de {settlement.amount}.",
        settlement_link(settlement),
    )


# ---------------------------------------------------------------------------
# Échéanciers
# ---------------------------------------------------------------------------

def is_party(settlement, user):
    return user.pk in (settlement.payer.user_id, settlement.receiver.user_id)


def can_manage_plan(settlement, user):
    return settlement.is_pending and (is_party(settlement, user) or is_admin(user, settlement.group))


@transaction.atomic
def save_plan(plan):
    """Crée ou modifie l'échéancier puis régénère les échéances (interdit après un paiement)."""
    if plan.pk and plan.has_payments():
        raise ValidationError("Modification impossible : une échéance a déjà été payée.")
    plan.total_amount = plan.settlement.amount
    plan.full_clean()
    plan.save()
    plan.generate_installments()
    predict_risk(plan)
    return plan


def delete_plan(plan):
    if plan.has_payments():
        raise ValidationError("Suppression impossible : une échéance a déjà été payée.")
    plan.delete()


def pay_installment(installment, user, amount):
    settlement = installment.plan.settlement
    if settlement.payer.user_id != user.pk:
        raise PermissionDenied("Seul le payeur enregistre le paiement de ses échéances.")
    if not settlement.is_pending or installment.plan.status != RepaymentPlan.Status.ACTIVE:
        raise ValidationError("Cet échéancier n'accepte plus de paiement.")
    installment.register_payment(amount)
    if installment.plan.status == RepaymentPlan.Status.COMPLETED:
        _notify(
            settlement.receiver,
            "plan_completed",
            f"L'échéancier de {settlement.payer.user} est entièrement payé : confirmez la réception.",
            settlement_link(settlement),
        )


# ---------------------------------------------------------------------------
# IA (1) : risque de retard
# ---------------------------------------------------------------------------

def predict_risk(plan):
    """Enregistre une RiskPrediction ; None si le modèle n'est pas disponible (non bloquant)."""
    from core.models import AIRequestLog

    features = risk.build_features(plan)
    try:
        probability, version, latency_ms = risk.predict_probability(features)
    except risk.RiskModelUnavailable as exc:
        logger.warning("%s", exc)
        return None
    AIRequestLog.objects.create(
        module="settlements",
        feature="m3.risk",
        latency_ms=latency_ms,
        success=True,
    )
    label = RiskPrediction.Label.HIGH if probability >= risk.THRESHOLD else RiskPrediction.Label.LOW
    return RiskPrediction.objects.create(
        plan=plan,
        probability=probability,
        label=label,
        model_version=version,
        features=features,
        latency_ms=latency_ms,
    )


DEFAULT_PLAN = {"installments_count": 3, "frequency": RepaymentPlan.Frequency.MONTHLY}


def suggest_plans(settlement, monthly_capacity, limit=3):
    """IA : évalue tous les échéanciers abordables avec le modèle de risque et propose les
    moins risqués, comparés à l'échéancier par défaut (3 mensualités)."""
    started = time.perf_counter()
    base = risk.settlement_features(settlement)
    ranked = risk.rank_plans(base, float(monthly_capacity))
    labels = dict(RepaymentPlan.Frequency.choices)
    for candidate in ranked:
        candidate["frequency_label"] = labels[candidate["frequency"]]
        candidate["percent"] = round(candidate["probability"] * 100)
    default_probability = risk.rank_plans(base, float("inf"))
    default = next(
        c for c in default_probability
        if c["installments_count"] == DEFAULT_PLAN["installments_count"]
        and c["frequency"] == DEFAULT_PLAN["frequency"]
    )
    return {
        "candidates": ranked[:limit],
        "feasible_count": len(ranked),
        "default_percent": round(default["probability"] * 100),
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def explain_prediction(prediction):
    """Contributions des variables à une prédiction enregistrée ; [] si le modèle manque."""
    try:
        return risk.explain(prediction.features)
    except (risk.RiskModelUnavailable, KeyError):
        return []


def parse_repayment_sentence(group, payer, text, user=None):
    """Langage naturel → champs du formulaire de remboursement (bénéficiaire = Membership)."""
    from . import nlp

    candidates = group.active_memberships().exclude(pk=payer.pk)
    members = [
        (m.pk, [n for n in (m.user.first_name, m.user.last_name, m.user.username,
                            m.user.get_full_name()) if n])
        for m in candidates
    ]
    result = nlp.parse_repayment(text, members, user=user, use_llm=bool(settings.LLM_API_KEY))
    labels = {m.pk: (m.user.get_full_name() or m.user.username) for m in candidates}
    return {
        "receiver": result["receiver"],
        "receiver_label": labels.get(result["receiver"], ""),
        "amount": str(result["amount"]) if result["amount"] is not None else None,
        "note": result["note"],
        "source": result["source"],
        "missing": result["missing"],
        "latency_ms": result["latency_ms"],
    }


# ---------------------------------------------------------------------------
# IA (2) : relance générée par le LLM, avec modèle de texte en repli
# ---------------------------------------------------------------------------

TONES = {
    "friendly": "amical",
    "neutral": "neutre",
    "firm": "ferme",
}

FALLBACK_TEMPLATES = {
    "friendly": (
        "Coucou {debtor} ! Petit rappel sans pression : il reste {amount} {currency} "
        "à me rembourser{late}. Merci beaucoup ! — {creditor}"
    ),
    "neutral": (
        "Bonjour {debtor}, il reste {amount} {currency} à rembourser à {creditor}{late}. "
        "Merci de régulariser dès que possible."
    ),
    "firm": (
        "Bonjour {debtor}, le remboursement de {amount} {currency} dû à {creditor} "
        "n'est toujours pas réglé{late}. Merci de procéder au paiement sans délai."
    ),
}
MAX_REMINDER_LENGTH = 600


def reminder_context(settlement, today=None):
    plan = getattr(settlement, "plan", None)
    amount = settlement.amount
    days_late = 0
    if plan:
        amount = plan.remaining_amount
        overdue = [i for i in plan.installments.all() if i.status != "paid"]
        days_late = max((i.days_late(today) for i in overdue), default=0)
    return {
        "debtor": settlement.payer.user.get_short_name() or settlement.payer.user.username,
        "creditor": settlement.receiver.user.get_short_name() or settlement.receiver.user.username,
        "amount": amount,
        "currency": settlement.group.currency,
        "days_late": days_late,
    }


def fallback_reminder(tone, context):
    late = f" (en retard de {context['days_late']} jours)" if context["days_late"] else ""
    return FALLBACK_TEMPLATES[tone].format(late=late, **context)


def generate_reminder(settlement, tone, user=None):
    """Retourne {message, source: llm|template, latency_ms}. Ne lève jamais : repli statique."""
    if tone not in TONES:
        raise ValidationError("Ton inconnu.")
    context = reminder_context(settlement)
    prompt = (
        "Rédige en français un court message de relance (2 à 3 phrases, sans objet ni signature "
        f"formelle) sur un ton {TONES[tone]}, pour une application de partage de dépenses.\n"
        f"Débiteur : {context['debtor']}\nBénéficiaire : {context['creditor']}\n"
        f"Montant restant : {context['amount']} {context['currency']}\n"
        f"Retard : {context['days_late']} jours\n"
        "Réponds uniquement avec le message."
    )
    started = time.perf_counter()
    try:
        text = call_llm("m3.reminder", prompt, module="settlements", user=user).strip()
        # Garde-fou : le message doit reprendre le montant (partie entière, le LLM peut écrire 12,5)
        if not text or len(text) > MAX_REMINDER_LENGTH or str(int(context["amount"])) not in text:
            raise LLMError("Relance générée invalide (vide, trop longue ou sans le montant).")
        source = "llm"
    except LLMError as exc:
        logger.info("Relance LLM indisponible, repli sur le modèle de texte : %s", exc)
        text = fallback_reminder(tone, context)
        source = "template"
    return {
        "message": text,
        "source": source,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def send_reminder(settlement, user, message) -> bool:
    """Envoie la relance au payeur via M6 ; False si les notifications sont indisponibles."""
    if settlement.receiver.user_id != user.pk:
        raise PermissionDenied("Seul le bénéficiaire peut relancer le payeur.")
    message = (message or "").strip()
    if not message or len(message) > MAX_REMINDER_LENGTH:
        raise ValidationError(f"Le message doit contenir entre 1 et {MAX_REMINDER_LENGTH} caractères.")
    return _notify(settlement.payer, "settlement_reminder", message, settlement_link(settlement))
